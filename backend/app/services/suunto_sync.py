"""Persisted conservative budgets and independent jobs; no provider I/O in API."""
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from sqlalchemy import select, text
from ..db import SuuntoSyncJob, SuuntoQuota, now
def iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).isoformat()


def enabled():
    return os.getenv('SUUNTO_ENABLED', '').lower() == 'true'


def queue_sync(db, owner):
    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(74198524)'))
    existing = db.scalar(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == owner,
        SuuntoSyncJob.status.in_(['queued', 'running'])).order_by(SuuntoSyncJob.id).limit(1))
    if existing:
        return existing, False
    job = SuuntoSyncJob(owner=owner, status='queued')
    db.add(job)
    db.flush()
    return job, True


def reserve_request(db, owner, current=None):
    current = current or now().timestamp()
    row = db.get(SuuntoQuota, owner)
    if row is None:
        row = SuuntoQuota(owner=owner, state={})
        db.add(row)
    state = dict(row.state)
    calls = [t for t in state.get('requests', []) if t > current-7*86400]
    if state.get('blocked_until', 0) > current:
        raise RuntimeError('Suunto collection paused until Retry-After expires.')
    if len(calls) >= 160:
        raise RuntimeError('Suunto rolling seven-day budget exhausted; retry later.')
    if sum(t > current-60 for t in calls) >= 10:
        raise RuntimeError('Suunto minute budget exhausted; retry later.')
    row.state = {**state, 'requests': [*calls, current]}


def record_backoff(db, owner, header):
    current = now().timestamp()
    try:
        delay = float(header)
        if not 0 <= delay < float('inf'):
            raise ValueError()
        until = current + delay
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(header)
            until = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
        except (TypeError, ValueError, OverflowError):
            until = current+86400
    row = db.get(SuuntoQuota, owner)
    if row is None:
        row = SuuntoQuota(owner=owner, state={})
        db.add(row)
    row.state = {**row.state, 'blocked_until': min(253402300799, max(current+60, until, row.state.get('blocked_until', 0)))}


def status(db, owner):
    job = db.scalar(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == owner).order_by(SuuntoSyncJob.id.desc()).limit(1))
    success = db.scalar(select(SuuntoSyncJob).where(SuuntoSyncJob.owner == owner,
        SuuntoSyncJob.status == 'complete').order_by(SuuntoSyncJob.id.desc()).limit(1))
    row = db.get(SuuntoQuota, owner)
    state = row.state if row else {}
    current = now().timestamp()
    calls = [t for t in state.get('requests', []) if t > current-7*86400]
    return {'configured': enabled(), 'source': 'suunto', 'last_successful_sync_at': iso(success.finished_at) if success else None,
            'sync': job_dto(job) if job else None,
            'quota': {'rolling_seven_day_limit': 160, 'requests_used': len(calls),
                      'requests_remaining': max(0, 160-len(calls)),
                      'blocked_until': datetime.fromtimestamp(state['blocked_until'], timezone.utc).isoformat() if state.get('blocked_until', 0) > current else None},
            'limitations': ['Summary archive only; no GPS, routes or continuous heart rate.',
                            'Coverage remains unconfirmed; missing workouts do not establish rest.']}


def job_dto(job):
    return {'job_id': job.id, 'status': job.status, 'detail': job.detail,
            'requested_at': iso(job.requested_at), 'finished_at': iso(job.finished_at)}
