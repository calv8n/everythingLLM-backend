from __future__ import annotations

import asyncio
import json

from .service import web_search


async def research_question(llm, bus, question: str, provider: str | None = None) -> dict:
    queries = await llm.json(
        f"Give up to 3 web search queries for:\n{question}\nReturn JSON list of strings."
    )
    if not isinstance(queries, list):
        queries = []

    for query in queries[:3]:
        await bus.publish({"type": "search_query", "content": str(query)})

    batches = await asyncio.gather(*[web_search(str(query), preferred=provider) for query in queries[:3]])
    seen: set[str] = set()
    results: list[dict] = []
    for batch in batches:
        for result in batch:
            if result.url in seen:
                continue
            seen.add(result.url)
            results.append({
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet,
                "source": result.source,
            })

    results = results[:6]
    if results:
        await bus.publish({
            "type": "search_results",
            "content": json.dumps(results, ensure_ascii=False),
        })

    context_parts = []
    for index, result in enumerate(results, start=1):
        context_parts.append(f"[{index}] {result['title']} ({result['url']})\n{result.get('snippet', '')}")
    context = "\n\n".join(context_parts)
    notes = await llm.text(
        f"Question: {question}\nSources:\n{context}\n\nWrite concise notes relevant to the question. Cite as [n]. Say if sources conflict or are thin."
    )
    return {"notes": notes, "sources": [{"n": i + 1, "title": result["title"], "url": result["url"]} for i, result in enumerate(results)]}
