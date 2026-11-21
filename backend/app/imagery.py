import base64
from io import BytesIO
from uuid import NAMESPACE_URL, uuid5

import httpx
from PIL import Image
from sqlalchemy import select

from app.config import Settings, settings
from app.db import Asset, Job, Session
from app.domain import Source
from app.presentations import begin_write
from app.providers import ProviderError, provider_for
from app.research import find_image
from app.storage import Storage, inspect_upload


IMAGE_PROVIDER_DEFAULTS = {
    "openai": ("https://api.openai.com/v1/images/generations", "gpt-image-1.5"),
    "openrouter": ("https://openrouter.ai/api/v1/images", "bytedance-seed/seedream-4.5"),
}
IMAGE_EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def image_api_key(config: Settings) -> str:
    api_key = config.image_gen_api_key.strip()
    if api_key:
        return api_key
    if config.image_provider == "openai":
        legacy_key = config.image_openai_api_key.strip()
        if legacy_key:
            return legacy_key
    return config.llm_api_key.strip() if provider_for(config) == config.image_provider else ""


def image_configured() -> bool:
    config = settings()
    return config.image_provider in IMAGE_PROVIDER_DEFAULTS and bool(image_api_key(config))


def render_image(job_id: str, token: str, index: int, owner_id: str, prompt: str) -> str | None:
    key = str(uuid5(NAMESPACE_URL, f"framecraft:{job_id}:slide-image:{index}"))
    with Session() as db:
        if db.get(Asset, key):
            return key
    config = settings()
    defaults = IMAGE_PROVIDER_DEFAULTS.get(config.image_provider)
    api_key = image_api_key(config)
    if defaults is None or not api_key:
        raise ProviderError("The image provider is unavailable.")
    default_url, default_model = defaults
    payload: dict[str, str | int] = {
        "model": config.image_model.strip() or default_model,
        "prompt": "Create a presentation illustration without captions, text or logos. " + prompt,
    }
    if config.image_provider == "openai":
        payload.update(size="1024x1024", quality="medium", n=1)
    try:
        response = httpx.post(
            config.image_gen_url.strip() or default_url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=config.ai_timeout_seconds,
        )
        response.raise_for_status()
        encoded = response.json()["data"][0]["b64_json"]
        if not isinstance(encoded, str):
            raise ValueError("Generated image data must be a base64 string.")
        if len(encoded) > config.asset_limit_bytes * 4 // 3 + 8:
            raise ValueError("Generated image exceeds the asset size limit.")
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) > config.asset_limit_bytes:
            raise ValueError("Generated image exceeds the asset size limit.")
        with Image.open(BytesIO(decoded), formats=list(IMAGE_EXTENSIONS)) as image:
            extension = IMAGE_EXTENSIONS[image.format]
        media_type, raw, _ = inspect_upload("illustration" + extension, decoded)
        if len(raw) > config.asset_limit_bytes:
            raise ValueError("Generated image exceeds the asset size limit.")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, IndexError, TypeError, OSError,
            Image.DecompressionBombError) as error:
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
