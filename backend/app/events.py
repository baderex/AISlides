"""Fan out committed SQL event changes to all editors on this API process."""
import asyncio
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db import PresentationRecord, Session, StudioEvent, engine

log = logging.getLogger(__name__)


def fetch_events(item_id, after, *, replay=True):
    with Session() as db:
        query = select(StudioEvent).where(StudioEvent.presentation_id == item_id, StudioEvent.id > after)
        order = StudioEvent.id.desc() if replay else StudioEvent.id.asc()
        rows = list(db.scalars(query.order_by(order).limit(settings().event_replay_limit)))
        if replay:
            rows.reverse()
        return [{"id": e.id, "presentation_id": e.presentation_id, "job_id": e.job_id, "type": e.type,
                 "timestamp": e.timestamp, "payload": e.payload} for e in rows]


def heads(ids):
    with Session() as db:
        latest = select(func.max(StudioEvent.id)).where(StudioEvent.presentation_id == PresentationRecord.id).scalar_subquery()
        query = select(PresentationRecord.id, latest).where(PresentationRecord.id.in_(ids))
        return dict(db.execute(query).all())


class EventWatcher:
    def __init__(self):
        self.listeners = defaultdict(set)
        self.task = None

    @asynccontextmanager
    async def subscribe(self, item_id):
        wake = asyncio.Event()
        self.listeners[item_id].add(wake)
        if self.task is None:
            self.task = asyncio.create_task(self.watch())
        try:
            yield wake
        finally:
            self.listeners[item_id].remove(wake)
            if not self.listeners[item_id]:
                del self.listeners[item_id]
            if not self.listeners:
                self.task.cancel()
                self.task = None

    def wake(self, key):
        for wake in self.listeners.get(key, ()):
            wake.set()

    async def listen_postgres(self):
        import psycopg
        url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        async with await psycopg.AsyncConnection.connect(url, autocommit=True, connect_timeout=3) as connection:
            await connection.execute("LISTEN framecraft_events")
            # Catch commits between the initial replay and LISTEN, including reconnects.
            for key in self.listeners:
                self.wake(key)
            async for notification in connection.notifies():
                self.wake(notification.payload)

    async def watch(self):
        previous = {}
        retry_at = 0
        loop = asyncio.get_running_loop()
        while True:
            if engine.dialect.name == "postgresql" and loop.time() >= retry_at:
                try:
                    await self.listen_postgres()
                except Exception:
                    log.warning("PostgreSQL event listener unavailable, using shared polling")
                retry_at = loop.time() + 30
            try:
                current = await run_in_threadpool(heads, list(self.listeners))
                for key, value in current.items():
                    if previous.get(key) != value:
                        self.wake(key)
                previous = current
            except Exception:
                log.exception("Could not read event cursors")
            await asyncio.sleep(1)


watcher = EventWatcher()


def event_response(item_id, cursor, request):
    async def stream():
        nonlocal cursor
        async with watcher.subscribe(item_id) as wake:
            yield "retry: 1500\n\n"
            replay = True
            while not await request.is_disconnected():
                wake.clear()
                batch = await run_in_threadpool(fetch_events, item_id, cursor, replay=replay)
                replay = False
                for event in batch:
                    cursor = event["id"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if len(batch) == settings().event_replay_limit:
                    continue
                while not wake.is_set():
                    try:
                        await asyncio.wait_for(wake.wait(), timeout=15)
                    except TimeoutError:
                        yield ": heartbeat\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
