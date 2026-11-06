from __future__ import annotations

import asyncio
import os
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx

from .base import SearchResult


class SearxngProvider:
    name = "searxng"

    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or os.getenv("SEARXNG_URL", "http://searxng:8080")).rstrip("/")

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{self.base}/search", params={"q": query, "format": "json"})
            response.raise_for_status()
        payload = response.json()
        return [
            SearchResult(
                title=item.get("title") or "Untitled result",
                url=item.get("url") or "",
                snippet=item.get("content") or item.get("snippet") or "",
                source=self.name,
            )
            for item in payload.get("results", [])[:max_results]
            if item.get("url")
        ]


class DuckDuckGoProvider:
    name = "duckduckgo"

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        def _run() -> list[SearchResult]:
            try:
                from ddgs import DDGS
            except ImportError:
                url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
                request = Request(url, headers={"User-Agent": "EverythingLLM/1.0"})
                with urlopen(request, timeout=12) as response:
                    html = response.read(2_000_000).decode("utf-8", errors="replace")

                class Parser(HTMLParser):
                    def __init__(self) -> None:
                        super().__init__(convert_charrefs=True)
                        self.results: list[dict[str, str]] = []
                        self._current: dict[str, str] | None = None
                        self._active: str | None = None

                    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                        attribute_map = dict(attrs)
                        classes = (attribute_map.get("class") or "").split()
                        if tag == "a" and "result__a" in classes:
                            self._current = {"title": "", "url": attribute_map.get("href") or "", "snippet": ""}
                            self._active = "title"
                        elif self._current and "result__snippet" in classes:
                            self._active = "snippet"

                    def handle_data(self, data: str) -> None:
                        if self._current and self._active:
                            current = self._current.get(self._active) or ""
                            self._current[self._active] = current + data

                    def handle_endtag(self, tag: str) -> None:
                        if self._current and self._active and self._active == "title" and tag == "a":
                            self.results.append(self._current)
                            self._current = self.results[-1]
                            self._active = None

                parser = Parser()
                parser.feed(html)
                results: list[SearchResult] = []
                for item in parser.results:
                    title = " ".join((item.get("title") or "").split())
                    snippet = " ".join((item.get("snippet") or "").split())
                    url_value = item.get("url") or ""
                    if title and url_value:
                        results.append(SearchResult(title=title[:300], url=url_value[:1500], snippet=snippet[:1200], source=self.name))
                    if len(results) >= max_results:
                        break
                return results

            rows = list(DDGS().text(query, max_results=max_results))
            return [
                SearchResult(
                    title=row.get("title") or "Untitled result",
                    url=row.get("href") or "",
                    snippet=row.get("body") or "",
                    source=self.name,
                )
                for row in rows
                if row.get("href")
            ]

        return await asyncio.to_thread(_run)


class TavilyProvider:
    name = "tavily"

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            return []
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "max_results": max_results},
            )
            response.raise_for_status()
        payload = response.json()
        return [
            SearchResult(
                title=item.get("title") or "Untitled result",
                url=item.get("url") or "",
                snippet=item.get("content") or "",
                source=self.name,
            )
            for item in payload.get("results", [])
            if item.get("url")
        ]
