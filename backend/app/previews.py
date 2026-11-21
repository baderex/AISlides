import hashlib

from app.db import SlidePreview, uid
from app.rendering import preview
from app.storage import Storage


def fingerprint(document, slide):
    value = document.aspect_ratio + slide.model_dump_json(exclude={"warnings", "notes", "source_ids"})
    return hashlib.sha256(value.encode()).hexdigest()


def put_preview(db, presentation_id, document, slide, raw):
    key = uid()
    Storage().put(key, raw, "image/png")
    db.add(SlidePreview(presentation_id=presentation_id, slide_id=slide.id, storage_key=key,
                        fingerprint=fingerprint(document, slide)))


def get_preview(db, presentation_id, document, slide):
    record = db.get(SlidePreview, (presentation_id, slide.id))
    storage = Storage()
    if record and record.fingerprint == fingerprint(document, slide):
        try:
            return storage.get(record.storage_key)
        except FileNotFoundError:
            pass
    return preview(document, slide, storage.get)
