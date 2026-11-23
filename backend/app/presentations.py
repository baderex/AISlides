from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select

from app.asset_lifecycle import track_document
from app.limits import admit_ai
from app.db import begin_write, Asset, Job, PresentationRecord, SkillRecord, StudioEvent, Version, now
from app.domain import Document, Group, Image, Slide, Source, read_document


class Conflict(ValueError):
    pass


def emit(db, item, event_type, message, job=None, **data):
    db.add(StudioEvent(presentation_id=item.id, job_id=job.id if job else None, type=event_type,
                       payload={"message": message, **data}))
    if db.bind.dialect.name == "postgresql":
        db.execute(select(func.pg_notify("framecraft_events", item.id)))


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
    track_document(db, item, version=bool(label))
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
    return [Source(id=str(uuid5(NAMESPACE_URL, f"framecraft:asset:{asset.id}")), title=asset.name,
                   excerpt=asset.extracted[:20000], asset_id=asset.id) for asset in assets]


def present_owned(db, item_id, owner_id, lock=False):
    query = select(PresentationRecord).where(PresentationRecord.id == item_id, PresentationRecord.owner_id == owner_id)
    return db.scalar(query.with_for_update() if lock else query)


def queue_generation(db, item, *, restart_failed=False):
    job = latest_job(db, item.id)
    if job:
        if job.state in {"queued", "running", "waiting"}:
            return job
        if not restart_failed or job.state not in {"failed", "cancelled"}:
            raise Conflict("This presentation already has a completed generation. Duplicate it to start a new deck.")
        admit_ai(db, item.owner_id)
        message = "Generation queued again from its saved checkpoint."
        if job.state == "failed":
            context = dict(job.context)
            if usage := context.get("usage"):
                context["usage"] = {**usage, "attempt_start_tokens": usage["input"] + usage["output"]}
            retry = context.get("layout_retry", {})
            if retry:
                context["layout_retry"] = {**retry, "attempt": 0}
            if job.checkpoint == "html_measure" and "html_candidate" not in context and retry.get("attempt", 0) >= 3:
                job.checkpoint = "html_slide"
            job.context = context
            message = "Retrying from the saved checkpoint with a fresh token allowance."
        job.state, job.error, job.cancelled = "queued", None, False
        job.lease = None
        job.lease_owner = None
        item.status = "CREATED"
        emit(db, item, "generation.resumed", message, job)
        return job
    admit_ai(db, item.owner_id)
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
