from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select, text
from .db import Base, engine, Session, Record, SyncJob, OWNER
from .services.sync_jobs import queue_sync
from .services.training import TrainingService, ProfileSettings, GuidanceRequest
from .db import TrainingProfile, now

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(title='Personal Coach', lifespan=lifespan)

@app.get('/api/health')
def health():
    with Session() as db:
        db.execute(text('SELECT 1'))
    return {'status': 'ok'}

@app.get('/api/dashboard')
def dashboard():
    with Session() as db:
        records = db.scalars(select(Record).where(Record.owner == OWNER).order_by(Record.day)).all()
        job = db.scalar(select(SyncJob).where(SyncJob.owner == OWNER).order_by(SyncJob.id.desc()).limit(1))
        return {'records': [{'kind': r.kind, 'day': r.day, 'data': r.payload} for r in records],
                'sync': None if job is None else {'status': job.status, 'detail': job.detail,
                  'requested_at': job.requested_at, 'finished_at': job.finished_at}}

@app.post('/api/sync', status_code=202)
def sync(request: Request):
    check_origin(request)
    with Session.begin() as db:
        job, created = queue_sync(db, OWNER)
        return {'job_id': job.id, 'status': job.status, 'created': created}


@app.get('/api/sync/{job_id}')
def sync_status(job_id: int):
    with Session() as db:
        job = db.scalar(select(SyncJob).where(SyncJob.id == job_id, SyncJob.owner == OWNER))
        if job is None:
            raise HTTPException(404, 'Sync job not found')
        return {'job_id': job.id, 'status': job.status, 'detail': job.detail,
                'requested_at': job.requested_at, 'finished_at': job.finished_at}


def check_origin(request):
    # Browser cross-origin writes are rejected; web UI and API share one origin.
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise HTTPException(403, 'Cross-site sync is not permitted')
    origin = request.headers.get('origin')
    if origin and origin != str(request.base_url).rstrip('/'):
        raise HTTPException(403, 'Origin mismatch')


@app.get('/api/training/profile')
def training_profile():
    return TrainingService(engine, OWNER).profile().model_dump()


@app.put('/api/training/profile')
def save_training_profile(settings: ProfileSettings, request: Request):
    check_origin(request)
    with Session.begin() as db:
        profile = db.get(TrainingProfile, OWNER)
        if profile is None:
            profile = TrainingProfile(owner=OWNER, settings={})
            db.add(profile)
        profile.settings, profile.updated_at = settings.model_dump(), now()
    return settings.model_dump()


@app.post('/api/training/guidance')
def training_guidance(checkin: GuidanceRequest, request: Request):
    check_origin(request)
    try:
        return TrainingService(engine, OWNER).guidance(checkin)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
