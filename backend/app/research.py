import httpx

from app.config import settings
from app.domain import Source
from app.providers import ProviderError


def collect(query: str) -> list[Source]:
    key = settings().research_api_key
    if not key:
        raise ProviderError("Web research requires a configured Brave Search API key.")
    try:
        response = httpx.get("https://api.search.brave.com/res/v1/web/search",
                             params={"q": query[:380], "count": 6, "text_decorations": False},
                             headers={"X-Subscription-Token": key, "Accept": "application/json"},
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
