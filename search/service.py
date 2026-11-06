from __future__ import annotations

import os
import time

from .base import SearchResult
from .providers import DuckDuckGoProvider, SearxngProvider, TavilyProvider

PROVIDERS = {
    "searxng": SearxngProvider(),
    "duckduckgo": DuckDuckGoProvider(),
    "tavily": TavilyProvider(),
}
DEFAULT_CHAIN = ["searxng", "duckduckgo"]
_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}
TTL = 900


def _normalize_provider(provider: str | None) -> str | None:
    if not provider:
        return None
    normalized = provider.strip().lower()
    aliases = {"ddg": "duckduckgo", "duck-duck-go": "duckduckgo", "duckduckgo": "duckduckgo"}
    return aliases.get(normalized, normalized)


async def web_search(query: str, preferred: str | None = None, max_results: int = 5) -> list[SearchResult]:
    cleaned_query = (query or "").strip()
    if not cleaned_query:
        return []

    provider = _normalize_provider(preferred)
    cache_key = f"{provider or 'auto'}:{cleaned_query.lower()}"
    cached = _CACHE.get(cache_key)
    if cached and time.time() - cached[0] < TTL:
        return cached[1]

    chain: list[str] = []
    if provider:
        chain.append(provider)
    for name in DEFAULT_CHAIN:
        if name not in chain:
            chain.append(name)
    if os.getenv("TAVILY_API_KEY"):
        chain.append("tavily")

    for name in chain:
        provider_instance = PROVIDERS.get(name)
        if provider_instance is None:
            continue
        try:
            results = await provider_instance.search(cleaned_query, max_results=max_results)
            if results:
                _CACHE[cache_key] = (time.time(), results)
                return results
        except Exception:
            continue
    return []
