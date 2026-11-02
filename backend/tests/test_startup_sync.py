import os
os.environ.setdefault('DATABASE_URL','sqlite://')
import unittest
from unittest.mock import patch
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base, Record, SyncJob, OWNER, now
from app.worker import prepare_startup, run_job
from app.services.sync_jobs import queue_sync

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite://')
        Base.metadata.create_all(self.engine)
        self.Session=sessionmaker(self.engine,expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_startup_refresh_even_after_recent_success(self):
        with self.Session.begin() as db:
            db.add(SyncJob(owner=OWNER,status='complete',finished_at=now()))
        with self.Session.begin() as db:
            job_id=prepare_startup(db)
        with self.Session() as db:
            self.assertEqual(db.get(SyncJob,job_id).status,'queued')
            self.assertEqual(db.scalar(select(func.count()).select_from(SyncJob)),2)

    def test_reuses_queued_job_and_recovers_interrupted_run(self):
        with self.Session.begin() as db:
            broken=SyncJob(owner=OWNER,status='running')
            queued=SyncJob(owner=OWNER,status='queued')
            db.add_all([broken,queued]);db.flush()
            broken_id,queued_id=broken.id,queued.id
        with self.Session.begin() as db:
            self.assertEqual(prepare_startup(db),queued_id)
            again,created=queue_sync(db,OWNER)
            self.assertEqual(again.id,queued_id)
            self.assertFalse(created)
        with self.Session() as db:
            self.assertEqual(db.get(SyncJob,broken_id).status,'failed')

    def test_empty_cache_repopulates_and_repeat_updates_without_duplicates(self):
        with self.Session.begin() as db:
            first,_=queue_sync(db,OWNER);first_id=first.id
        with patch('app.worker.Session',self.Session),patch('app.worker.OuraClient') as client_type,patch.dict(os.environ,{'SYNC_LOOKBACK_DAYS':'90'}):
            client=client_type.return_value
            client.records.return_value=[{'id':'record','day':'2026-10-30','score':50}]
            run_job(first_id)
            requested=client.records.call_args_list[0].args
            from datetime import date
            self.assertEqual((date.fromisoformat(requested[2])-date.fromisoformat(requested[1])).days,89)
            with self.Session.begin() as db:
                second,_=queue_sync(db,OWNER);second_id=second.id
            client.records.return_value=[{'id':'record','day':'2026-10-30','score':60}]
            run_job(second_id)
        with self.Session() as db:
            rows=db.scalars(select(Record)).all()
            self.assertEqual(len(rows),6)
            self.assertTrue(all(r.payload['score']==60 for r in rows))
            self.assertEqual(db.get(SyncJob,second_id).status,'complete')

    def test_failed_provider_does_not_clear_cache(self):
        with self.Session.begin() as db:
            db.add(Record(owner=OWNER,source='oura',kind='daily_readiness',external_id='old',day='2026-10-01',payload={'score':55}))
            job,_=queue_sync(db,OWNER);job_id=job.id
        with patch('app.worker.Session',self.Session),patch('app.worker.OuraClient') as client_type:
            client_type.return_value.records.side_effect=RuntimeError('Oura unavailable')
            with self.assertRaises(RuntimeError):run_job(job_id)
        with self.Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Record)),1)
        client_type.return_value.close.assert_called_once()
