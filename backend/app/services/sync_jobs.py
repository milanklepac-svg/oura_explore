"""One refresh request at a time for each local owner."""
from sqlalchemy import select, text
from ..db import SyncJob


def queue_sync(db, owner):
    # Serialize API and worker enqueue decisions. The lock is released at commit.
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(74198522)'))
    existing = db.scalar(select(SyncJob).where(
        SyncJob.owner == owner, SyncJob.status.in_(['queued', 'running']))
        .order_by(SyncJob.id).limit(1))
    if existing:
        return existing, False
    job = SyncJob(owner=owner, status='queued')
    db.add(job)
    db.flush()
    return job, True
