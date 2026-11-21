import base64
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.config import settings
from app.db import Asset, Job, Session
from app.presentations import begin_write
from app.providers import ProviderError
from app.storage import Storage, inspect_upload


def image_configured():
    conf = settings()
    return conf.image_provider == "openai" and bool(conf.image_openai_api_key or conf.openai_api_key)


def render_image(job_id: str, token: str, index: int, owner_id: str, prompt: str) -> str | None:
    key = str(uuid5(NAMESPACE_URL, f"framecraft:{job_id}:slide-image:{index}"))
    with Session() as db:
        if db.get(Asset, key):
            return key
    config = settings()
    api_key = config.image_openai_api_key or config.openai_api_key
    try:
        response = httpx.post("https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": config.image_model, "size": "1024x1024", "quality": "medium", "n": 1,
                  "prompt": "Create a presentation illustration without captions, text or logos. " + prompt},
            timeout=config.ai_timeout_seconds)
        response.raise_for_status()
        encoded = response.json()["data"][0]["b64_json"]
        if len(encoded) > config.asset_limit_bytes * 4 // 3 + 8:
            raise ValueError("Generated image exceeds the asset size limit.")
        media_type, raw, _ = inspect_upload("illustration.png", base64.b64decode(encoded, validate=True))
    except (httpx.HTTPError, ValueError, KeyError, IndexError, OSError) as error:
        raise ProviderError("The image service did not return a usable illustration.") from error
    with Session.begin() as db:
        begin_write(db)
        from sqlalchemy import select
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.lease != token or job.cancelled:
            return None
        if not db.get(Asset, key):
            Storage().put(key, raw, media_type)
            db.add(Asset(id=key, owner_id=owner_id, name=f"Generated slide {index + 1}.png", media_type=media_type, size=len(raw)))
    return key
