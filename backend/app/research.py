from io import BytesIO

import httpx
from PIL import Image

from app.config import settings
from app.domain import Source
from app.providers import ProviderError
from app.storage import inspect_upload


def collect(query: str) -> list[Source]:
    configuration = settings()
    if not configuration.research_api_key:
        raise ProviderError("Web research requires a configured Brave Search API key.")
    try:
        response = httpx.get(configuration.research_api_url,
                             params={"q": query[:380], "count": 6, "text_decorations": False},
                             headers={"X-Subscription-Token": configuration.research_api_key, "Accept": "application/json"},
                             timeout=15, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise ProviderError("Web research is temporarily unavailable. Check the research integration.") from error
    results = []
    for result in response.json().get("web", {}).get("results", []):
        try:
            results.append(Source(title=result["title"][:220], url=result["url"],
                                  excerpt=result.get("description", "")[:1600]))
        except (KeyError, ValueError):
            continue
    return results


def find_image(query: str) -> tuple[Source, bytes, float]:
    configuration = settings()
    if not configuration.research_api_key:
        raise ProviderError("Web image search requires a configured Brave Search API key.")
    query = " ".join(query.split()[:50])[:380]
    portrait_requested = "portrait" in query.casefold()
    with httpx.Client(timeout=6, follow_redirects=False) as client:
        try:
            response = client.get(
                configuration.research_image_api_url,
                params={"q": query, "count": 8, "safesearch": "strict"},
                headers={"X-Subscription-Token": configuration.research_api_key, "Accept": "application/json"},
            )
            response.raise_for_status()
            results = response.json()["results"]
            if not isinstance(results, list):
                raise ValueError("Invalid result list")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise ProviderError("Web image search is unavailable. Check the Brave Image Search integration.") from error

        candidates = []
        for result in results[:8]:
            try:
                source = Source(title=(result.get("title") or query)[:200], url=result["url"])
                if source.url is None:
                    continue
                url = httpx.URL(result["thumbnail"]["src"])
                # Download only from Brave's image proxy. The origin URL is used for attribution.
                if (url.scheme != "https" or url.host != "imgs.search.brave.com" or url.port is not None or url.userinfo):
                    continue
                host = source.url.host or ""
                reference = host.endswith((".gov", ".mil", ".wikimedia.org", ".wikipedia.org"))
                candidates.append((reference, source, url))
            except (httpx.InvalidURL, AttributeError, KeyError, TypeError, ValueError):
                continue

        fallback = None
        for _, source, url in sorted(candidates, key=lambda candidate: candidate[0], reverse=True):
            try:
                raw, ratio = download_thumbnail(client, url, configuration.asset_limit_bytes)
                if portrait_requested and ratio > 1.05:
                    fallback = fallback or (source, raw, ratio)
                    continue
                return source, raw, ratio
            except (httpx.HTTPError, KeyError, ValueError, OSError, Image.DecompressionBombError):
                continue
        if fallback:
            return fallback
    raise ProviderError("Web image search found no usable image for this slide.")


def download_thumbnail(client: httpx.Client, url: httpx.URL, limit: int) -> tuple[bytes, float]:
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
    with client.stream("GET", url) as response:
        response.raise_for_status()
        extension = extensions[response.headers.get("content-type", "").split(";")[0].lower()]
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            if len(data) + len(chunk) > limit:
                raise ValueError("Image exceeds the asset size limit")
            data.extend(chunk)
    _, raw, _ = inspect_upload("thumbnail" + extension, bytes(data))
    with Image.open(BytesIO(raw)) as image:
        ratio = image.width / image.height
        if min(image.size) < 200 or not .3 <= ratio <= 3 or len(raw) > limit:
            raise ValueError("Image dimensions or size are unsuitable for a slide")
    return raw, ratio
