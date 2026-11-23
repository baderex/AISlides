import re
from html import unescape
from io import BytesIO

import httpx
from PIL import Image

from app.config import settings
from app.domain import Source
from app.limits import provider_call
from app.providers import ProviderError
from app.storage import inspect_upload


def search_excerpt(fragments, limit):
    return "\n".join(dict.fromkeys(unescape(text).strip() for text in fragments if isinstance(text, str) and text.strip()))[:limit]


def collect_context(url, query, count, headers):
    with provider_call(settings().research_cost_usd * 1_000_000):
        response = httpx.get(url, params={"q": query, "maximum_number_of_urls": count,
                             "maximum_number_of_tokens": 4096}, headers=headers, timeout=15, follow_redirects=False)
    response.raise_for_status()
    terms = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    sources = []
    for result in response.json().get("grounding", {}).get("generic", [])[:count]:
        try:
            snippets = [text for text in result["snippets"] if isinstance(text, str) and text.strip()]
            # Keep passages that mention the requested metrics and periods together, including structured tables.
            snippets.sort(key=lambda text: len(terms.intersection(re.findall(r"[a-z0-9]{3,}", text.lower()))), reverse=True)
            if snippets:
                sources.append(Source(title=unescape(result["title"])[:220], url=result["url"],
                                      excerpt=search_excerpt(snippets, 3000)))
        except (KeyError, TypeError, ValueError):
            continue
    return sources


def collect(query: str, *, count: int = 6, detailed: bool = False) -> list[Source]:
    configuration = settings()
    if not configuration.research_api_key:
        raise ProviderError("Web research requires a configured Brave Search API key.")
    query = query[:380]
    headers = {"X-Subscription-Token": configuration.research_api_key, "Accept": "application/json"}
    if detailed and configuration.research_context_api_url:
        try:
            sources = collect_context(configuration.research_context_api_url, query, count, headers)
            if sources:
                return sources
        except (httpx.HTTPError, ValueError):
            pass
    params = {"q": query, "count": count, "text_decorations": False}
    if detailed:
        params["extra_snippets"] = True
    try:
        with provider_call(configuration.research_cost_usd * 1_000_000):
            response = httpx.get(configuration.research_api_url,
                                 params=params,
                                 headers=headers,
                                 timeout=15, follow_redirects=False)
        response.raise_for_status()
    except httpx.HTTPError as error:
        raise ProviderError("Web research is temporarily unavailable. Check the research integration.") from error
    results = []
    for result in response.json().get("web", {}).get("results", []):
        try:
            excerpts = [result.get("description", ""), *(result.get("extra_snippets") or [])]
            results.append(Source(title=unescape(result["title"])[:220], url=result["url"],
                                  excerpt=search_excerpt(excerpts, 3000 if detailed else 1600)))
        except (KeyError, TypeError, ValueError):
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
            with provider_call(configuration.research_cost_usd * 1_000_000):
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
