from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class _DuckDuckGoResultsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: tuple[str, str] | None = None
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attributes.get("href") or "", "snippet": ""}
            self._active = ("title", tag)
        elif self._current and "result__snippet" in classes:
            self._active = ("snippet", tag)

    def handle_data(self, data: str) -> None:
        if self._active and self._current:
            key, _ = self._active
            self._current[key] += data

    def handle_endtag(self, tag: str) -> None:
        if self._active and self._active[1] == tag:
            key, _ = self._active
            self._active = None
            if key == "title" and self._current:
                self.results.append(self._current)
                self._current = self.results[-1]
        if tag in {"div", "td"} and self._current and self._current.get("snippet"):
            self._current = None


def search_web(query: str, *, max_results: int = 4, timeout: int = 12) -> list[dict[str, str]]:
    """Search public web results with DuckDuckGo's HTML endpoint."""
    url = "https://html.duckduckgo.com/html/?" + urlencode({"q": query})
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; TaskRunnerResearch/1.0)"},
    )
    with urlopen(request, timeout=timeout) as response:
        html = response.read(2_000_000).decode("utf-8", errors="replace")

    parser = _DuckDuckGoResultsParser()
    parser.feed(html)
    results = []
    for result in parser.results:
        title = " ".join(result["title"].split())
        snippet = " ".join(result["snippet"].split())
        link = result["url"]
        if title and link:
            results.append({"title": title[:300], "url": link[:1500], "snippet": snippet[:1200]})
        if len(results) >= max_results:
            break
    return results
