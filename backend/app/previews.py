import hashlib

from app.db import SlidePreview, uid
from app.rendering import fit_slide_text, preview
from app.storage import Storage


def fingerprint(document, slide):
    value = "text-layout-2:" + document.aspect_ratio + slide.model_dump_json(exclude={"warnings", "notes", "source_ids"})
    return hashlib.sha256(value.encode()).hexdigest()


def put_preview(db, presentation_id, document, slide, raw):
    key = uid()
    Storage().put(key, raw, "image/png")
    db.add(SlidePreview(presentation_id=presentation_id, slide_id=slide.id, storage_key=key,
                        fingerprint=fingerprint(document, slide)))


def get_preview(db, presentation_id, document, slide, *, artwork=None):
    record = db.get(SlidePreview, (presentation_id, slide.id))
    storage = Storage()
    if artwork is None and record and record.fingerprint == fingerprint(document, slide):
        try:
            return storage.get(record.storage_key)
        except FileNotFoundError:
            pass
    fitted = slide.model_copy(deep=True)
    fit_slide_text(document, fitted)
    return preview(document, fitted, storage.get, artwork=artwork)
