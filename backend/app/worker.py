import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from .db import Base, engine, Session, Record, SyncJob, OWNER, now
from .oura import OuraClient, KINDS
from .services.sync_jobs import queue_sync

LOCK = 74198521

def run_job(job_id):
    client = OuraClient()
    try:
        days = int(os.getenv('SYNC_LOOKBACK_DAYS', '90'))
        if not 1 <= days <= 365:
            raise RuntimeError('SYNC_LOOKBACK_DAYS must be between 1 and 365')
        end = datetime.now(ZoneInfo(os.getenv('TIMEZONE', 'Europe/Belgrade'))).date()
        start = end - timedelta(days=days-1)
        count = 0
        for kind in KINDS:
            for payload in client.records(kind, str(start), str(end)):
                external_id = payload.get('id')
                if not external_id:
                    continue
                day = payload.get('day') or str(payload.get('start_datetime', ''))[:10]
                with Session.begin() as db:
                    record = db.scalar(select(Record).where(Record.owner == OWNER, Record.source == 'oura',
                                                            Record.kind == kind, Record.external_id == external_id))
                    if record is None:
                        record = Record(owner=OWNER, source='oura', kind=kind, external_id=external_id)
                        db.add(record)
                    record.day, record.payload, record.updated_at = day, payload, now()
                count += 1
        with Session.begin() as db:
            job = db.get(SyncJob, job_id)
            job.status, job.finished_at, job.detail = 'complete', now(), f'{count} records processed'
    finally:
        client.close()

def review_plan_after_sync():
    from .services.planning import PlanningService
    try:
        PlanningService(engine, OWNER, os.getenv('TIMEZONE', 'Europe/Belgrade')).review()
    except Exception:
        # No private payloads or connection strings in worker output. Retry on next sync.
        print('Local plan review failed; Oura sync remains complete. Retry Review plan in the app.', flush=True)


def prepare_startup(db):
    for job in db.scalars(select(SyncJob).where(SyncJob.owner == OWNER, SyncJob.status == 'running')):
        job.status, job.detail, job.finished_at = 'failed', 'Worker restarted before sync completed.', now()
    db.flush()
    # A recent successful run must not suppress the refresh on this startup.
    return queue_sync(db, OWNER)[0].id


def main():
    Base.metadata.create_all(engine)
    # One worker owns refresh-token rotation; PostgreSQL releases the lock on disconnect.
    with engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(:id)'), {'id': LOCK}):
            raise RuntimeError('Another Oura worker is already running.')
        lock.commit()
        with Session.begin() as db:
            prepare_startup(db)
        while True:
            with Session.begin() as db:
                job = db.scalar(select(SyncJob).where(SyncJob.owner == OWNER, SyncJob.status == 'queued').order_by(SyncJob.id).limit(1))
                latest = db.scalar(select(SyncJob).where(SyncJob.owner == OWNER).order_by(SyncJob.id.desc()).limit(1))
                if job is None and (latest is None or (now() - latest.requested_at).total_seconds() > int(os.getenv('SYNC_INTERVAL_SECONDS', '3600'))):
                    job, _ = queue_sync(db, OWNER)
                job_id = job.id if job else None
                if job:
                    job.status = 'running'
            if job_id:
                try:
                    run_job(job_id)
                    # Planning failure must not turn a successful provider sync into a failure.
                    review_plan_after_sync()
                except Exception as error:
                    detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
                    with Session.begin() as db:
                        job = db.get(SyncJob, job_id)
                        job.status, job.detail, job.finished_at = 'failed', detail, now()
            time.sleep(5)

if __name__ == '__main__':
    main()
