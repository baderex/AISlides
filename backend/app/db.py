from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, JSON, Boolean, ForeignKey, Index, Integer, String, Text, create_engine, event, false
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password: Mapped[str] = mapped_column(Text, default="")


class LoginSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires: Mapped[int]


class SkillRecord(Base):
    __tablename__ = "skills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    deleted: Mapped[bool] = mapped_column(Boolean, server_default=false())


class PresentationRecord(Base):
    __tablename__ = "presentations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="CREATED")
    request: Mapped[dict] = mapped_column(JSON)
    document: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[str] = mapped_column(String(40), default=now)
    updated: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (Index("ix_presentations_owner_updated", "owner_id", "updated", "id"),)


class Version(Base):
    __tablename__ = "versions"
    presentation_id: Mapped[str] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict] = mapped_column(JSON)
    label: Mapped[str] = mapped_column(String(100))
    created: Mapped[str] = mapped_column(String(40), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    presentation_id: Mapped[str] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    checkpoint: Mapped[str] = mapped_column(String(20), default="analyze")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    lease: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created: Mapped[str] = mapped_column(String(40), default=now)
    updated: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (Index("ix_jobs_queue", "state", "lease_until", "created"),)


class StudioEvent(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    presentation_id: Mapped[str] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    timestamp: Mapped[str] = mapped_column(String(40), default=now)
    __table_args__ = (Index("ix_events_presentation_cursor", "presentation_id", "id"), {"sqlite_autoincrement": True})


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int]
    extracted: Mapped[str] = mapped_column(Text, default="")
    created: Mapped[str] = mapped_column(String(40), default=now)


class ExportRecord(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    presentation_id: Mapped[str] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int]
    format: Mapped[str] = mapped_column(String(8))
    title: Mapped[str | None] = mapped_column(String(200))
    created: Mapped[str] = mapped_column(String(40), default=now)


class UsageBucket(Base):
    __tablename__ = "usage_buckets"
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    used: Mapped[int] = mapped_column(BigInteger)


class AISlot(Base):
    __tablename__ = "ai_slots"
    kind: Mapped[str] = mapped_column(String(12))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires: Mapped[int]


class AssetReference(Base):
    __tablename__ = "asset_references"
    scope: Mapped[str] = mapped_column(String(100), primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), primary_key=True, index=True)
    presentation_id: Mapped[str | None] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    skill_id: Mapped[str | None] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), index=True)


class PendingDelete(Base):
    __tablename__ = "pending_deletes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)


class ArtifactJob(Base):
    __tablename__ = "artifact_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    presentation_id: Mapped[str | None] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict] = mapped_column(JSON)
    cache_key: Mapped[str | None] = mapped_column(String(64), index=True)
    result: Mapped[dict | None] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease: Mapped[str | None] = mapped_column(String(36))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[str] = mapped_column(String(40), default=now)
    updated: Mapped[str] = mapped_column(String(40), default=now)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    seen: Mapped[int]


class SlidePreview(Base):
    __tablename__ = "slide_previews"
    presentation_id: Mapped[str] = mapped_column(ForeignKey("presentations.id", ondelete="CASCADE"), primary_key=True)
    slide_id: Mapped[str] = mapped_column(String(70), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(36))
    fingerprint: Mapped[str] = mapped_column(String(64))
    created: Mapped[str] = mapped_column(String(40), default=now)


def begin_write(db):
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def lock_account(db, owner_id):
    from sqlalchemy import select
    db.execute(select(User.id).where(User.id == owner_id).with_for_update())


def database_engine():
    url = make_url(settings().database_url)
    sqlite = url.get_backend_name() == "sqlite"
    if sqlite:
        from pathlib import Path
        url = url.set(drivername="sqlite")
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    elif url.drivername in {"postgresql", "postgresql+asyncpg"}:
        url = url.set(drivername="postgresql+psycopg")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"timeout": 15} if sqlite else {})
    if sqlite:
        @event.listens_for(engine, "connect")
        def sqlite_options(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    return engine


engine = database_engine()
Session = sessionmaker(engine, expire_on_commit=False)
