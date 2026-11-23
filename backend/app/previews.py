import hashlib
import json

from app.db import SlidePreview, uid
from app.rendering import fit_slide_text, preview
from app.storage import Storage


def fingerprint(document, slide):
    value = json.dumps(["text-layout-3", document.model_dump(mode="json", exclude={"slides"}),
                        slide.model_dump(mode="json", exclude={"warnings", "notes"})],
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def put_preview(db, presentation_id, document, slide, raw):
    key = uid()
    Storage().put(key, raw, "image/png")
    db.add(SlidePreview(presentation_id=presentation_id, slide_id=slide.id, storage_key=key,
                        fingerprint=fingerprint(document, slide)))


def cached_preview(db, presentation_id, document, slide):
    record = db.get(SlidePreview, (presentation_id, slide.id))
    storage = Storage()
    if record and record.fingerprint == fingerprint(document, slide):
        try:
            return storage.get(record.storage_key)
        except FileNotFoundError:
            pass
    return None


def render_preview(document, slide, artwork=None):
    fitted = slide.model_copy(deep=True)
    fit_slide_text(document, fitted)
    return preview(document, fitted, Storage().get, artwork=artwork)
