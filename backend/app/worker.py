import argparse
import logging
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.artifacts import claim_artifact, run_isolated
from app.asset_lifecycle import collect_orphans, drain_deletes, reconcile_storage, schedule_deletes
from app.config import settings
from app.db import (AISlot, ArtifactJob, ExportRecord, Job, LoginSession, Session,
                    StudioEvent, UsageBucket, WorkerHeartbeat, uid)
from app.queue import claim, heartbeat, requested_job
from app.workflow import advance

log = logging.getLogger(__name__)


def cleanup():
    collect_orphans()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings().artifact_retention_days)).isoformat()
    with Session.begin() as db:
        db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen < int(time.time()) - 86400))
        db.execute(delete(AISlot).where(AISlot.expires < int(time.time())))
        db.execute(delete(UsageBucket).where(UsageBucket.window > 0, UsageBucket.window < int(time.time()) - 172800))
        event_cutoff = (datetime.now(timezone.utc) - timedelta(days=settings().event_retention_days)).isoformat()
        db.execute(delete(StudioEvent).where(StudioEvent.timestamp < event_cutoff))
        db.execute(delete(LoginSession).where(LoginSession.expires < int(time.time())))
        expired = list(db.scalars(select(ExportRecord).where(ExportRecord.created < cutoff).limit(100).with_for_update(skip_locked=True)))
        schedule_deletes(db, (entry.id for entry in expired))
        for entry in expired:
            db.delete(entry)
        finished = db.scalars(select(ArtifactJob).where(ArtifactJob.updated < cutoff,
                              ArtifactJob.state.in_(("complete", "failed"))).limit(100).with_for_update(skip_locked=True))
        for job in finished:
            if job.result and job.result.get("file_key"):
                schedule_deletes(db, [job.result["file_key"]])
            db.delete(job)
    drain_deletes()


def ping(worker_id, stop, active):
    while not stop.is_set():
        try:
            with Session() as db:
                ticket, model = active.copy()
                heartbeat(db, worker_id, ticket, model)
        except Exception:
            log.exception("Could not write worker heartbeat")
        stop.wait(min(4, settings().lease_seconds / 3))


def work(once=False):
    worker_id = uid()
    with Session() as db:
        heartbeat(db, worker_id)
    stop = threading.Event()
    active = [None, Job]
    thread = threading.Thread(target=ping, args=(worker_id, stop, active), daemon=True)
    thread.start()
    prefer_artifact = True
    last_cleanup = last_reconciliation = float("-inf")
    try:
        while True:
            try:
                if time.monotonic() - last_cleanup > 300:
                    last_cleanup = time.monotonic()
                    cleanup()
                if time.monotonic() - last_reconciliation > 86400:
                    last_reconciliation = time.monotonic()
                    reconcile_storage()
                preferred = requested_job() if not once else None
                with Session() as db:
                    model = ArtifactJob
                    ticket = claim_artifact(db, worker_id) if prefer_artifact else None
                    if not ticket:
                        model, ticket = Job, claim(db, worker_id, preferred)
                    if not ticket and not prefer_artifact:
                        model, ticket = ArtifactJob, claim_artifact(db, worker_id)
                if ticket:
                    prefer_artifact = model is Job
                    active[:] = [ticket, model]
                    try:
                        if model is Job:
                            advance(*ticket)
                        else:
                            run_isolated(*ticket)
                    finally:
                        active[:] = [None, Job]
                elif once:
                    return
                else:
                    stop.wait(.65)
            except Exception:
                log.exception("Worker could not process a checkpoint")
                if once:
                    raise
                stop.wait(2)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        thread.join(timeout=5)
        with Session.begin() as db:
            db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.id == worker_id))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Drain available work then exit")
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    work(parser.parse_args().once)
