from sqlalchemy import select

from app.db import Asset, Job, PresentationRecord, SkillRecord, StudioEvent, Version, now
from app.domain import Document, Group, Image, Slide, Source, read_document


class Conflict(ValueError):
    pass


def emit(db, item, event_type, message, job=None, **data):
    db.add(StudioEvent(presentation_id=item.id, job_id=job.id if job else None, type=event_type,
                       payload={"message": message, **data}))


def latest_job(db, item_id):
    return db.scalar(select(Job).where(Job.presentation_id == item_id).order_by(Job.created.desc()).limit(1))


def persist(db, item, document: Document, label: str | None = None):
    from app.exhibit_evidence import display_evidence

    checked = [display_evidence(document, slide) for slide in document.slides]
    document = document.model_copy(update={"slides": [slide if len(slide.elements) <= 100 else previous
                                                      for slide, previous in zip(checked, document.slides)]})
    item.document = document.model_dump(mode="json")
    item.title = document.title
    item.revision += 1
    item.updated = now()
    if label:
        db.add(Version(presentation_id=item.id, revision=item.revision, document=item.document, label=label))


def summary(item):
    return {"id": item.id, "title": item.title, "status": item.status, "revision": item.revision,
            "created": item.created, "updated": item.updated, "slide_count": len(item.document["slides"]),
            "aspect_ratio": item.document["aspect_ratio"], "thumbnail": (item.document["slides"] or [None])[0]}


def details(item):
    return {**summary(item), "document": read_document(item.document).model_dump(mode="json"), "request": item.request}


def assets_for(db, owner_id, ids):
    if not ids:
        return []
    return list(db.scalars(select(Asset).where(Asset.id.in_(ids), Asset.owner_id == owner_id)))


def skill_for(db, owner_id, key, *, include_deleted=False):
    item = db.get(SkillRecord, key) if key else None
    return item if item and item.owner_id in {None, owner_id} and (include_deleted or not item.deleted) else None


def image_ids(db, owner_id, ids):
    return {asset.id for asset in assets_for(db, owner_id, ids) if asset.media_type.startswith("image/")}


def referenced_images(slides: list[Slide]):
    def walk(elements):
        for el in elements:
            if isinstance(el, Image):
                yield el.asset_id
            if isinstance(el, Group):
                yield from walk(el.elements)
    return {key for slide in slides for key in walk(slide.elements)}


def asset_sources(assets):
    return [Source(title=asset.name, excerpt=asset.extracted[:3000], asset_id=asset.id) for asset in assets]


def present_owned(db, item_id, owner_id, lock=False):
    query = select(PresentationRecord).where(PresentationRecord.id == item_id, PresentationRecord.owner_id == owner_id)
    return db.scalar(query.with_for_update() if lock else query)


def begin_write(db):
    if db.bind.dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def queue_generation(db, item, *, restart_failed=False):
    job = latest_job(db, item.id)
    if job:
        if job.state in {"queued", "running", "waiting"}:
            return job
        if not restart_failed or job.state not in {"failed", "cancelled"}:
            raise Conflict("This presentation already has a completed generation. Duplicate it to start a new deck.")
        job.state, job.error, job.cancelled = "queued", None, False
        job.lease = None
        job.lease_owner = None
        item.status = "CREATED"
        emit(db, item, "generation.resumed", "Generation queued again from its saved checkpoint.", job)
        return job
    job = Job(presentation_id=item.id, context={"layout_engine":"auto"})
    db.add(job)
    db.flush()
    emit(db, item, "generation.started", "Generation queued.", job)
    return job


def assert_editable(item, revision):
    if revision != item.revision:
        raise Conflict("This deck changed in another window. Refresh and try again.")
    if item.status not in {"CREATED", "COMPLETED", "FAILED", "CANCELLED"}:
        raise Conflict("Wait for generation to finish or cancel it before saving changes.")
