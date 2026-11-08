import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
OWNER = os.getenv('OWNER_ID', 'local-owner')

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Record(Base):
    __tablename__ = 'wearable_records'
    __table_args__ = (UniqueConstraint('owner', 'source', 'kind', 'external_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String, default='oura')
    kind: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String)
    day: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SyncJob(Base):
    __tablename__ = 'sync_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default='queued')
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(String)


class TrainingProfile(Base):
    __tablename__ = 'training_profiles'
    owner: Mapped[str] = mapped_column(String, primary_key=True)
    settings: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RacePlan(Base):
    __tablename__ = 'race_plans'
    owner: Mapped[str] = mapped_column(String, primary_key=True)
    settings: Mapped[dict] = mapped_column(JSON)
    sessions: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PlanAudit(Base):
    __tablename__ = 'plan_audit'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int]
    event: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class SuuntoSyncJob(Base):
    __tablename__ = 'suunto_sync_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default='queued')
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[str | None] = mapped_column(String)

class SuuntoQuota(Base):
    __tablename__ = 'suunto_quota'
    owner: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
