import argparse
import logging
import signal
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.config import settings
from app.db import ExportRecord, LoginSession, Session, WorkerHeartbeat, uid
from app.queue import claim, heartbeat, requested_job
from app.storage import Storage
from app.workflow import advance

log = logging.getLogger(__name__)


def cleanup():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings().artifact_retention_days)).isoformat()
    with Session.begin() as db:
        db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.seen < int(time.time()) - 86400))
        db.execute(delete(LoginSession).where(LoginSession.expires < int(time.time())))
        expired = list(db.scalars(select(ExportRecord).where(ExportRecord.created < cutoff).limit(100)))
        if expired:
            storage = Storage()
            for entry in expired:
                storage.delete(entry.id)
                db.delete(entry)


def ping(worker_id, stop):
    while not stop.is_set():
        try:
            with Session() as db:
                heartbeat(db, worker_id)
        except Exception:
            log.exception("Could not write worker heartbeat")
        stop.wait(4)


def work(once=False):
    worker_id = uid()
    with Session() as db:
        heartbeat(db, worker_id)
    stop = threading.Event()
    thread = threading.Thread(target=ping, args=(worker_id, stop), daemon=True)
    thread.start()
    last_cleanup = 0
    try:
        while True:
            try:
                if time.monotonic() - last_cleanup > 300:
                    cleanup()
                    last_cleanup = time.monotonic()
                preferred = requested_job() if not once else None
                with Session() as db:
                    ticket = claim(db, worker_id, preferred)
                if ticket:
                    advance(*ticket)
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
