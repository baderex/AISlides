import logging
import time

from sqlalchemy import and_, case, or_, select, update

from app.config import settings
from app.db import Job, WorkerHeartbeat, now, uid

log = logging.getLogger(__name__)
HEARTBEAT_TIMEOUT = 18


def redis_client():
    import redis
    return redis.Redis.from_url(settings().redis_url, socket_connect_timeout=2, socket_timeout=3)


def notify(job_id: str):
    if settings().queue_uses_redis:
        try:
            redis_client().lpush("framecraft:jobs", job_id)
        except Exception:
            log.warning("Redis dispatch failed. SQL polling will recover queued work.")


def requested_job() -> str | None:
    if settings().queue_uses_redis:
        try:
            entry = redis_client().brpop("framecraft:jobs", timeout=2)
            return entry[1].decode("ascii") if entry else None
        except Exception:
            pass
    return None


def heartbeat(db, worker_id: str):
    entry = db.get(WorkerHeartbeat, worker_id)
    seen = int(time.time())
    if entry:
        entry.seen = seen
    else:
        db.add(WorkerHeartbeat(id=worker_id, seen=seen))
    db.commit()


def ready(db) -> bool:
    if settings().queue_uses_redis:
        redis_client().ping()
    return db.scalar(select(WorkerHeartbeat.id).where(WorkerHeartbeat.seen >= int(time.time()) - HEARTBEAT_TIMEOUT).limit(1)) is not None


def claim(db, worker_id=None, preferred=None):
    current = int(time.time())
    owner_healthy = select(WorkerHeartbeat.id).where(
        WorkerHeartbeat.id == Job.lease_owner, WorkerHeartbeat.seen >= current - HEARTBEAT_TIMEOUT
    ).exists()
    recoverable = or_(Job.lease_until < current, and_(Job.lease_owner.is_not(None), ~owner_healthy))
    allowed = and_(Job.cancelled.is_(False), or_(Job.state == "queued", and_(Job.state == "running", recoverable)))
    ordering = [Job.updated, Job.created, Job.id]
    if preferred:
        ordering.insert(0, case((Job.id == preferred, 0), else_=1))
    candidate = select(Job.id).where(allowed).order_by(*ordering).limit(1).scalar_subquery()
    token = uid()
    lease = max(settings().lease_seconds, 3 * settings().ai_timeout_seconds + 60)
    result = db.execute(update(Job).where(Job.id == candidate, allowed)
                        .values(state="running", lease=token, lease_owner=worker_id, lease_until=current + lease, updated=now())
                        .returning(Job.id)).scalar_one_or_none()
    db.commit()
    return (result, token) if result else None
