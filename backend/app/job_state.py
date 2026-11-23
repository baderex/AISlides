from sqlalchemy import select, update

from app.asset_lifecycle import track
from app.db import Job, PresentationRecord, Session, now
from app.domain import read_document
from app.presentations import begin_write, emit, persist
from app.previews import put_preview
from app.queue import notify


def start_status(job_id, token, status, event_type, message, **payload):
    with Session.begin() as db:
        begin_write(db)
        item = lock_presentation(db, job_id)
        if not item:
            return False
        guarded = db.execute(update(Job).where(Job.id == job_id, Job.lease == token, Job.cancelled.is_(False))
                             .values(updated=now()).returning(Job.presentation_id)).scalar_one_or_none()
        if not guarded:
            return False
        item.status = status
        item.updated = now()
        emit(db, item, event_type, message, db.get(Job, job_id), **payload)
        return True


def commit(job_id, token, checkpoint, state, status, context, events, document=None, version_label=None, error=None, png=None):
    with Session.begin() as db:
        begin_write(db)
        item = lock_presentation(db, job_id)
        if not item:
            return
        guarded = db.execute(update(Job).where(Job.id == job_id, Job.lease == token, Job.cancelled.is_(False))
                             .values(checkpoint=checkpoint, state=state, context=context, lease=None, lease_owner=None, lease_until=0,
                                     error=error, updated=now()).returning(Job.presentation_id)).scalar_one_or_none()
        if not guarded:
            return
        job = db.get(Job, job_id)
        track(db, item.owner_id, "job:" + job.id, context, presentation_id=item.id)
        item.status, item.updated = status, now()
        if document:
            if png:
                put_preview(db, item.id, document, document.slides[-1], png)
            persist(db, item, document, version_label)
        elif version_label:
            persist(db, item, read_document(item.document), version_label)
        for event_type, message, payload in events:
            emit(db, item, event_type, message, job, **payload)
    if state == "queued":
        notify(job_id)


def lock_presentation(db, job_id):
    return db.scalar(select(PresentationRecord).where(
        PresentationRecord.id == select(Job.presentation_id).where(Job.id == job_id).scalar_subquery()
    ).with_for_update())
