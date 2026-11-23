"""Durable bounded work queue for file conversion, previews and exports."""
import hashlib
import json
import logging
import os
import subprocess
import sys
import time

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, or_, select, update

from app.artifact_render import build
from app.asset_lifecycle import schedule_deletes, store_asset, track
from app.config import settings
from app.db import ArtifactJob, Asset, AssetReference, ExportRecord, Session, now, uid
from app.export_review import ReviewBusy, ReviewUnavailable
from app.limits import LimitExceeded, lock_account, lock_artifact_queue
from app.presentations import begin_write, present_owned
from app.queue import notify
from app.storage import Storage

log = logging.getLogger(__name__)


def enqueue(owner_id, presentation_id, kind, params, raw=None):
    with Session.begin() as db:
        begin_write(db)
        if presentation_id and not present_owned(db, presentation_id, owner_id, lock=True):
            raise HTTPException(404, "Presentation not found.")
        lock_account(db, owner_id)
        # A shared row serializes the capacity check across API processes.
        lock_artifact_queue(db)
        cache_key = None
        if kind in {"preview", "artwork"}:
            cache_key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            cached = db.scalar(select(ArtifactJob.id).where(
                ArtifactJob.presentation_id == presentation_id, ArtifactJob.kind == kind,
                ArtifactJob.cache_key == cache_key, ArtifactJob.state != "failed").limit(1))
            if cached:
                return cached
        active = db.scalar(select(func.count()).select_from(ArtifactJob).where(ArtifactJob.state.in_(("queued", "running"))))
        if active >= settings().artifact_queue_capacity:
            raise LimitExceeded("The artifact queue is full. Try again shortly.")
        if raw is not None:
            params = {**params, "input_key": uid()}
            Storage().put(params["input_key"], raw, "application/octet-stream")
        job = ArtifactJob(owner_id=owner_id, presentation_id=presentation_id, kind=kind, params=params, cache_key=cache_key)
        db.add(job)
        db.flush()
        if presentation_id:
            track(db, owner_id, "artifact:" + job.id, params, presentation_id=presentation_id)
    notify(job.id)
    return job.id


def claim_artifact(db, worker_id):
    current = int(time.time())
    available = or_(ArtifactJob.state == "queued", and_(ArtifactJob.state == "running", ArtifactJob.lease_until < current))
    candidate = select(ArtifactJob.id).where(available).order_by(ArtifactJob.created, ArtifactJob.id).limit(1).scalar_subquery()
    token = uid()
    key = db.scalar(update(ArtifactJob).where(ArtifactJob.id == candidate, available).values(
        state="running", lease=token, lease_owner=worker_id, lease_until=current + settings().lease_seconds,
        attempts=ArtifactJob.attempts + 1, updated=now()).returning(ArtifactJob.id))
    db.commit()
    return (key, token) if key else None


def finish(job, db, state, result):
    job.state, job.result, job.updated = state, result, now()
    job.lease, job.lease_owner, job.lease_until = None, None, 0
    if state != "queued":
        if input_key := job.params.get("input_key"):
            schedule_deletes(db, [input_key])
        db.execute(delete(AssetReference).where(AssetReference.scope == "artifact:" + job.id))
        job.params = {}


def fail(key, token, message, status, retry=False):
    with Session.begin() as db:
        begin_write(db)
        job = db.scalar(select(ArtifactJob).where(ArtifactJob.id == key, ArtifactJob.lease == token).with_for_update())
        if job:
            finish(job, db, "queued" if retry and job.attempts < 2 else "failed", {"detail": message, "status": status})


def execute(key, token):
    with Session() as db:
        job = db.get(ArtifactJob, key)
        if not job or job.lease != token:
            return
        params, kind = job.params, job.kind
        owner_id, presentation_id = job.owner_id, job.presentation_id
    try:
        raw, media_type, result = build(kind, params)
        with Session.begin() as db:
            begin_write(db)
            if presentation_id and not present_owned(db, presentation_id, owner_id, lock=True):
                return
            job = db.scalar(select(ArtifactJob).where(ArtifactJob.id == key, ArtifactJob.lease == token).with_for_update())
            if not job:
                return
            if kind == "conversion":
                asset = Asset(owner_id=job.owner_id, name=result["name"], size=len(raw),
                              media_type=media_type, extracted=result["extracted"])
                store_asset(db, asset, raw)
                result = {"id": asset.id, "name": asset.name, "size": asset.size, "media_type": asset.media_type}
            else:
                Storage().put(key, raw, media_type)
                if kind in {"export", "review"}:
                    db.add(ExportRecord(id=key, presentation_id=job.presentation_id, revision=result["revision"],
                                        format=params["format"], title=result["title"]))
                    result.update(id=key, download=f"/api/v1/presentations/{job.presentation_id}/exports/{key}")
                else:
                    result["file_key"] = key
            finish(job, db, "complete", result)
    except (ReviewUnavailable, ReviewBusy, LimitExceeded, ValueError) as error:
        status = 415 if kind == "conversion" and isinstance(error, ValueError) else (
            429 if isinstance(error, (ReviewBusy, LimitExceeded)) else 503)
        fail(key, token, str(error), status, retry=isinstance(error, ReviewBusy))
    except Exception:
        log.exception("Artifact job failed id=%s", key)
        fail(key, token, "The worker could not finish the requested file.", 503, retry=True)


def run_isolated(key, token):
    with Session() as db:
        job = db.get(ArtifactJob, key)
        if not job or job.lease != token:
            return
        exhausted = job.attempts > 2
    if exhausted:
        fail(key, token, "The worker could not finish the requested file after two attempts.", 503)
        return
    try:
        subprocess.run([sys.executable, "-m", "app.artifacts", key, token], check=True,
                       timeout=settings().artifact_timeout_seconds,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        fail(key, token, "File processing timed out or was interrupted.", 503, retry=True)


def forget_image(key, owner_id):
    with Session.begin() as db:
        db.execute(delete(ArtifactJob).where(ArtifactJob.id == key, ArtifactJob.owner_id == owner_id,
                   ArtifactJob.kind.in_(("preview", "artwork")), ArtifactJob.state == "complete"))


def status(key, owner_id):
    with Session() as db:
        job = db.scalar(select(ArtifactJob).where(ArtifactJob.id == key, ArtifactJob.owner_id == owner_id))
        if not job:
            return None
        return {"id": key, "kind": job.kind, "state": job.state, "attempts": job.attempts, "result": job.result}


if __name__ == "__main__":
    execute(*sys.argv[1:])
