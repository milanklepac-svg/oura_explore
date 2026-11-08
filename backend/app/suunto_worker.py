"""Independent daily/manual Suunto collection; sole refresh-token owner."""
import os
import time
from datetime import timedelta
from sqlalchemy import select, text
from .db import Base, engine, Session, Record, SuuntoSyncJob, OWNER, now
from .suunto import SuuntoClient, RateLimited
from .services.suunto_sync import enabled, queue_sync, reserve_request, record_backoff


def run_job(job_id):
    budget = 0
    def reserve():
        nonlocal budget
        if budget >= 10:
            raise RuntimeError('Suunto per-run request budget exhausted; retry later.')
        with Session.begin() as db:
            reserve_request(db, OWNER)
        budget += 1
    client = None
    try:
        client = SuuntoClient(reserve)
        days = int(os.getenv('SUUNTO_SYNC_LOOKBACK_DAYS', '90'))
        if not 1 <= days <= 365:
            raise RuntimeError('SUUNTO_SYNC_LOOKBACK_DAYS must be between 1 and 365.')
        end = now()
        start = end-timedelta(days=days)
        count = 0
        for payload in client.records(int(start.timestamp()*1000), int(end.timestamp()*1000)):
            with Session.begin() as db:
                record = db.scalar(select(Record).where(Record.owner == OWNER, Record.source == 'suunto',
                    Record.kind == 'workout', Record.external_id == payload['record_id']))
                if record is None:
                    record = Record(owner=OWNER, source='suunto', kind='workout', external_id=payload['record_id'])
                    db.add(record)
                record.day, record.payload, record.updated_at = payload['day'], payload, now()
            count += 1
        with Session.begin() as db:
            job = db.get(SuuntoSyncJob, job_id)
            job.status, job.finished_at, job.detail = 'complete', now(), f'{count} running summaries processed'
        # Plan review is local and must never invalidate completed provider collection.
        try:
            from .services.planning import PlanningService
            PlanningService(engine, OWNER, os.getenv('TIMEZONE', 'Europe/Belgrade')).review()
        except Exception:
            print('Local plan review failed; Suunto collection remains complete. Retry Review plan in the app.', flush=True)
    except Exception as error:
        with Session.begin() as db:
            if isinstance(error, RateLimited):
                record_backoff(db, OWNER, error.retry_after)
            job = db.get(SuuntoSyncJob, job_id)
            job.status, job.finished_at = 'failed', now()
            job.detail = str(error) if isinstance(error, RuntimeError) else 'Suunto collection failed; check worker configuration.'
    finally:
        if client:
            client.close()


def main():
    Base.metadata.create_all(engine)
    with engine.connect() as lock:
        if not lock.scalar(text('SELECT pg_try_advisory_lock(74198523)')):
            raise RuntimeError('Another Suunto worker is already running.')
        lock.commit()
        with Session.begin() as db:
            for job in db.scalars(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == OWNER, SuuntoSyncJob.status == 'running')):
                job.status, job.finished_at, job.detail = 'failed', now(), 'Worker restarted before collection completed.'
        while True:
            if enabled():
                with Session.begin() as db:
                    latest = db.scalar(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == OWNER).order_by(SuuntoSyncJob.id.desc()).limit(1))
                    interval = max(86400, int(os.getenv('SUUNTO_SYNC_INTERVAL_SECONDS', '86400')))
                    if latest is None or now().timestamp()-latest.requested_at.replace(tzinfo=now().tzinfo).timestamp() >= interval:
                        queue_sync(db, OWNER)
                    job = db.scalar(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == OWNER, SuuntoSyncJob.status == 'queued').order_by(SuuntoSyncJob.id).limit(1))
                    job_id = job.id if job else None
                    if job:
                        job.status = 'running'
                if job_id:
                    run_job(job_id)
            time.sleep(5)


if __name__ == '__main__':
    main()
