from __future__ import annotations

from backend.search.service import web_search as _web_search


def search_web(query: str, *, max_results: int = 4, timeout: int = 12, provider: str | None = None) -> list[dict[str, str]]:
    """Search public web results using the configured backend provider chain."""
    import asyncio

    async def _run() -> list[dict[str, str]]:
        results = await _web_search(query, preferred=provider, max_results=max_results)
        return [
            {"title": result.title, "url": result.url, "snippet": result.snippet}
            for result in results
        ]

    return asyncio.run(_run())
