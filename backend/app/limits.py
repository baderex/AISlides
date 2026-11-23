"""Shared admission counters and provider cost reservations, in UTC windows."""
import math
import time
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import settings
from app.db import AISlot, Job, PresentationRecord, Session, UsageBucket, begin_write, lock_account


class ProviderError(Exception):
    pass


class LimitExceeded(ProviderError):
    pass


billing_owner = ContextVar("billing_owner", default=None)


@contextmanager
def billing_account(owner_id):
    token = billing_owner.set(owner_id)
    try:
        yield
    finally:
        billing_owner.reset(token)


def lock_artifact_queue(db):
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    db.execute(insert(UsageBucket).values(key="artifact-admission", window=0, used=0).on_conflict_do_nothing())
    db.execute(select(UsageBucket.key).where(UsageBucket.key == "artifact-admission",
                                            UsageBucket.window == 0).with_for_update())


def consume(db, key, seconds, quantity, ceiling, message):
    if quantity > ceiling:
        raise LimitExceeded(message)
    window = int(time.time()) // seconds * seconds
    insert = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
    statement = insert(UsageBucket).values(key=key, window=window, used=quantity)
    accepted = db.scalar(statement.on_conflict_do_update(
        index_elements=[UsageBucket.key, UsageBucket.window],
        set_={"used": UsageBucket.used + quantity}, where=UsageBucket.used <= ceiling - quantity
    ).returning(UsageBucket.used))
    if accepted is None:
        raise LimitExceeded(message)
    return window


def admit_ai(db, owner_id):
    config = settings()
    lock_account(db, owner_id)
    active = db.scalar(select(func.count()).select_from(Job).join(PresentationRecord).where(
        PresentationRecord.owner_id == owner_id, Job.state.in_(("queued", "running", "waiting"))))
    chats = db.scalar(select(func.count()).select_from(AISlot).where(
        AISlot.owner_id == owner_id, AISlot.kind == "chat", AISlot.expires > int(time.time())))
    if active + chats >= config.account_ai_concurrency:
        raise LimitExceeded("Your account has reached its concurrent AI request limit.")
    consume(db, "ai-minute:" + owner_id, 60, 1, config.account_ai_requests_per_minute,
            "Your account has reached its per-minute AI request limit.")
    consume(db, "ai-day:" + owner_id, 86400, 1, config.account_ai_requests_daily,
            "Your account has reached its daily AI request limit.")


@contextmanager
def chat_slot(owner_id):
    with Session.begin() as db:
        begin_write(db)
        admit_ai(db, owner_id)
        slot = AISlot(owner_id=owner_id, kind="chat", expires=int(time.time()) + 6 * settings().ai_timeout_seconds + 120)
        db.add(slot)
        db.flush()
    try:
        with billing_account(owner_id):
            yield
    finally:
        with Session.begin() as db:
            db.execute(delete(AISlot).where(AISlot.id == slot.id))


@contextmanager
def provider_call(microdollars):
    config = settings()
    cost = math.ceil(microdollars)
    owner_id = billing_owner.get()
    buckets = []
    slot = None
    with Session.begin() as db:
        begin_write(db)
        if owner_id:
            lock_account(db, owner_id)
            active = db.scalar(select(func.count()).select_from(AISlot).where(
                AISlot.owner_id == owner_id, AISlot.kind == "provider", AISlot.expires > int(time.time())))
            if active >= config.account_ai_concurrency:
                raise LimitExceeded("Your account has reached its concurrent provider call limit.")
            slot = AISlot(owner_id=owner_id, kind="provider", expires=int(time.time()) + 6 * config.ai_timeout_seconds + 120)
            db.add(slot)
            key = "spend:" + owner_id
            window = consume(db, key, 86400, cost, int(config.account_daily_budget_usd * 1_000_000),
                             "Your account has reached its daily provider spend limit.")
            buckets.append((key, window))
        window = consume(db, "provider", 86400, cost, int(config.provider_daily_budget_usd * 1_000_000),
                         "The studio has reached its daily provider spend limit.")
        buckets.append(("provider", window))
    try:
        yield buckets, cost
    finally:
        if slot:
            with Session.begin() as db:
                db.execute(delete(AISlot).where(AISlot.id == slot.id))


def settle_spend(reservation, actual):
    buckets, reserved = reservation
    difference = math.ceil(actual) - reserved
    if difference:
        with Session.begin() as db:
            for key, window in buckets:
                db.execute(update(UsageBucket).where(UsageBucket.key == key, UsageBucket.window == window)
                           .values(used=UsageBucket.used + difference))
