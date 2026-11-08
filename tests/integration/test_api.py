"""HTTP integration against the disposable PostgreSQL-backed application."""
import os
import time
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import select, func
from app.db import Session, Record, TrainingProfile, RacePlan, PlanAudit, SyncJob, OWNER


def wait_for_complete(client, job_id=None):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        data = client.get(f'/api/sync/{job_id}').json() if job_id else client.get('/api/dashboard').json()['sync']
        if data and data['status'] == 'complete':
            return data
        if data and data['status'] == 'failed':
            raise AssertionError('Synthetic worker failed')
        time.sleep(0.25)
    raise AssertionError('Synthetic sync timed out')


class PersistedApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = httpx.Client(base_url=os.environ['TEST_API_URL'], timeout=15)
        cls.today = datetime.now(ZoneInfo('Europe/Belgrade')).date()
        wait_for_complete(cls.client)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_foreign_owner_data_is_not_exposed(self):
        with Session.begin() as db:
            db.add(Record(owner='synthetic-other-owner', source='oura', kind='daily_readiness',
                          external_id='synthetic-other-owner-record', day=str(self.today),
                          payload={'id': 'synthetic-other-owner-record', 'score': 1}))
            job = SyncJob(owner='synthetic-other-owner', status='complete')
            db.add(job)
            db.flush()
            foreign_job_id = job.id
        dashboard = self.client.get('/api/dashboard').json()
        self.assertFalse(any(row['data']['id'] == 'synthetic-other-owner-record' for row in dashboard['records']))
        self.assertEqual(self.client.get(f'/api/sync/{foreign_job_id}').status_code, 404)

    def test_health_dashboard_and_real_worker_upserts(self):
        self.assertEqual(self.client.get('/api/health').json(), {'status': 'ok'})
        before = self.client.get('/api/dashboard').json()['records']
        self.assertEqual(len(before), 77)
        self.assertTrue(all(row['data']['id'].startswith('synthetic-') for row in before))
        queued = self.client.post('/api/sync')
        self.assertEqual(queued.status_code, 202)
        wait_for_complete(self.client, queued.json()['job_id'])
        self.assertEqual(sorted(self.client.get('/api/dashboard').json()['records'], key=lambda r: (r['day'], r['kind'])),
                         sorted(before, key=lambda r: (r['day'], r['kind'])))
        with Session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Record).where(Record.owner == OWNER)), 77)
        self.assertEqual(self.client.get('/api/sync/999999').status_code, 404)

    def test_profile_persists_and_guidance_does_not_change_it(self):
        profile = self.client.get('/api/training/profile').json()
        profile.update(goal='10k', goal_note='Synthetic integration profile', current_weekly_km=24,
                       available_days=['mon', 'wed', 'fri', 'sun'])
        self.assertEqual(self.client.put('/api/training/profile', json=profile).status_code, 200)
        self.assertEqual(self.client.get('/api/training/profile').json(), profile)

        with Session() as db:
            self.assertEqual(db.get(TrainingProfile, OWNER).settings, profile)
        guidance = self.client.post('/api/training/guidance', json={
            'day': str(self.today), 'planned_session': 'easy', 'fatigue': 2,
            'soreness': 1, 'illness_or_injury': False})
        self.assertEqual(guidance.status_code, 200)
        self.assertIn('recommendation', guidance.json())
        self.assertEqual(self.client.get('/api/training/profile').json(), profile)
        invalid = dict(profile, current_weekly_km=-1)
        self.assertEqual(self.client.put('/api/training/profile', json=invalid).status_code, 422)
        self.assertEqual(self.client.get('/api/training/profile').json(), profile)

    def test_walking_context_uses_recorded_walks(self):
        response = self.client.get('/api/walking/context')
        self.assertEqual(response.status_code, 200)
        context = response.json()
        self.assertEqual(context['period']['end'], str(self.today))
        self.assertEqual(len(context['walks']), 1)
        self.assertEqual(context['walks'][0]['classification'], 'uncertain')
        self.assertTrue(context['walks'][0]['data_gaps'])
        self.assertFalse(context['policy']['automatic_progression'])
        self.assertEqual(self.client.get('/api/walking/context?day=bad').status_code, 422)
        self.assertEqual(self.client.get('/api/walking/context', params={'day':str(self.today+timedelta(days=1))}).status_code, 422)

    def test_race_plan_revision_and_audit_persist(self):
        settings = {'start': str(self.today), 'weekly_km': 24, 'peak_weekly_km': 32,
                    'current_long_km': 7, 'easy_pace_minutes': 6.5,
                    'running_days': sorted(set(['mon', 'wed', 'fri', 'sun',
                                                ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'][self.today.weekday()]])),
                    'long_day': 'sun',
                    'races': [{'name': 'Synthetic 10K', 'day': str(self.today + timedelta(days=70)),
                               'distance': '10k', 'priority': 'main', 'target_minutes': 60}]}
        saved = self.client.post('/api/plan', json={'settings': settings, 'expected_revision': 0})
        self.assertEqual(saved.status_code, 200)
        plan = saved.json()['plan']
        session = next(s for s in plan['sessions'] if s['current']['kind'] not in ('rest', 'race'))
        changed = self.client.post('/api/plan/session', json={
            'day': session['day'], 'status': 'completed', 'expected_revision': plan['revision']})
        self.assertEqual(changed.status_code, 200)
        revision = changed.json()['plan']['revision']
        self.assertEqual(self.client.post('/api/plan/mode', json={
            'mode': 'review', 'expected_revision': plan['revision']}).status_code, 409)
        self.assertEqual(self.client.get('/api/plan').json()['plan']['revision'], revision)
        with Session() as db:
            row = db.get(RacePlan, OWNER)
            self.assertEqual(row.revision, revision)
            self.assertEqual(next(s for s in row.sessions if s['day'] == session['day'])['status'], 'completed')
            self.assertGreaterEqual(db.scalar(select(func.count()).select_from(PlanAudit)), 2)
        self.assertEqual(self.client.get('/api/running/context').status_code, 200)

    def test_cross_origin_writes_are_rejected(self):
        headers = {'Origin': 'https://untrusted.example', 'Sec-Fetch-Site': 'cross-site'}
        profile = self.client.get('/api/training/profile').json()
        for method, path, body in [('POST', '/api/sync', None),
                                   ('PUT', '/api/training/profile', profile),
                                   ('POST', '/api/training/guidance', {'day': str(self.today)}),
                                   ('POST', '/api/plan/mode', {'mode': 'review', 'expected_revision': 1})]:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path, json=body, headers=headers).status_code, 403)

    def test_unconfigured_suunto_safe_status_and_independent_jobs(self):
        status = self.client.get('/api/suunto/status')
        self.assertEqual(status.status_code, 200)
        data = status.json()
        self.assertFalse(data['configured'])
        self.assertEqual(data['quota']['rolling_seven_day_limit'], 160)
        self.assertEqual(self.client.post('/api/suunto/sync').status_code, 409)
        self.assertEqual(self.client.get('/api/health').json(), {'status': 'ok'})
        self.assertEqual(self.client.get('/api/suunto/sync/999999').status_code, 404)
        from app.db import SuuntoSyncJob
        with Session.begin() as db:
            job = SuuntoSyncJob(owner='synthetic-other-owner', status='complete')
            db.add(job)
            db.flush()
            foreign_id = job.id
        self.assertEqual(self.client.get(f'/api/suunto/sync/{foreign_id}').status_code, 404)
        self.assertEqual(self.client.post('/api/suunto/sync', headers={
            'Origin': 'https://untrusted.example', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)


    def test_zz_suunto_worker_postgres_upsert_and_recovery_sources(self):
        from unittest.mock import patch
        from app import suunto_worker
        from app.suunto import normalize
        from app.services.suunto_sync import queue_sync
        from app.services.oura_queries import OuraQueries
        from app.db import SuuntoSyncJob
        day = self.today-timedelta(days=1)
        start = datetime.combine(day, datetime.min.time(), ZoneInfo('Europe/Belgrade')).replace(hour=8)
        payload = normalize({'workoutKey':'synthetic-suunto-run', 'activityId':1,
            'startTime':int(start.timestamp()*1000),
            'stopTime':int((start+timedelta(minutes=30)).timestamp()*1000),
            'totalTime':1740, 'totalDistance':5200, 'timeOffsetInMinutes':60})
        class SyntheticSuunto:
            def __init__(self, reserve): self.reserve=reserve
            def close(self): pass
            def records(self, since, until):
                self.reserve()
                yield payload
        for _ in range(2):
            with Session.begin() as db:
                job, _ = queue_sync(db, OWNER)
                job.status='running'
                job_id=job.id
            with patch.object(suunto_worker,'SuuntoClient',SyntheticSuunto):
                suunto_worker.run_job(job_id)
            with Session() as db:
                self.assertEqual(db.get(SuuntoSyncJob,job_id).status,'complete')
                self.assertEqual(db.scalar(select(func.count()).select_from(Record).where(
                    Record.owner==OWNER,Record.source=='suunto',Record.external_id=='synthetic-suunto-run')),1)
        result=self.client.get('/api/running/context').json()
        run=next(r for r in result['runs'] if r['record_id']=='synthetic-suunto-run')
        self.assertEqual(run['source'],'suunto')
        self.assertEqual(run['km'],5.2)
        self.assertEqual(run['reconciliation'],'matched_suunto_preferred')
        self.assertEqual(run['provenance'],['suunto','oura'])
        from app.db import engine
        recovery=OuraQueries(engine,OWNER).recovery_context(self.today)
        self.assertEqual(recovery['metadata']['source'],'oura')
        self.assertEqual(recovery['day']['readiness_score'],84)
        partial=self.client.get('/api/running/context?include_today=true').json()
        self.assertEqual(partial['end'],str(self.today))
