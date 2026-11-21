import base64
from io import BytesIO
from uuid import NAMESPACE_URL, uuid5

import httpx
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db import Asset, Job, Session
from app.domain import Source
from app.presentations import begin_write
from app.providers import ProviderError
from app.research import find_image
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
    asset = Asset(id=key, owner_id=owner_id, name=f"Generated slide {index + 1}.png", media_type=media_type, size=len(raw))
    return key if save_image(job_id, token, asset, raw) else None


def source_web_image(job_id: str, token: str, index: int, owner_id: str, query: str) -> tuple[Source, float] | None:
    key = str(uuid5(NAMESPACE_URL, f"framecraft:{job_id}:slide-web-image:{index}"))
    with Session() as db:
        asset = db.get(Asset, key)
        if asset:
            with Image.open(BytesIO(Storage().get(key))) as image:
                ratio = image.width / image.height
            return Source(id=key, title=asset.name, url=asset.extracted, asset_id=key), ratio

    source, raw, ratio = find_image(query)
    asset = Asset(id=key, owner_id=owner_id, name=source.title, media_type="image/png", size=len(raw), extracted=str(source.url))
    if not save_image(job_id, token, asset, raw):
        return None
    return source.model_copy(update={"id": key, "asset_id": key}), ratio


def save_image(job_id: str, token: str, asset: Asset, raw: bytes) -> bool:
    with Session.begin() as db:
        begin_write(db)
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.lease != token or job.cancelled:
            return False
        if not db.get(Asset, asset.id):
            Storage().put(asset.id, raw, asset.media_type)
            db.add(asset)
    return True
