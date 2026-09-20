"""Real-time web research via SerpAPI (rewritten from tools/research.py)."""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from app.editorial import config

log = logging.getLogger(__name__)

SERP_API_URL = "https://serpapi.com/search.json"
MAX_RESULTS_PER_QUERY = 6
QUERY_DELAY_SECONDS = 2  # be polite to SerpAPI

_sleep = time.sleep  # patched in tests


def search_serp(query: str, api_key: str) -> list[dict[str, Any]]:
    params: dict[str, str | int] = {
        "q": query,
        "api_key": api_key,
        "engine": "google",
        "num": MAX_RESULTS_PER_QUERY,
        "gl": "in",
        "hl": "en",
        "safe": "active",
    }
    try:
        resp = httpx.get(SERP_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        organic = resp.json().get("organic_results", [])
        log.info("Query %r → %s results", query[:60], len(organic))
        return list(organic)
    except (httpx.HTTPError, ValueError) as e:
        log.error("SerpAPI request failed: %s", e)
        return []


def extract_snippets(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in results:
        snippet = str(r.get("snippet", "")).strip()
        if not snippet:
            continue
        out.append(
            {
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "snippet": snippet,
                "source": r.get("source", ""),
                "position": r.get("position", 0),
            }
        )
    return out


def score_relevance(snippet: str, topic: str) -> float:
    topic_words = set(topic.lower().split())
    if not topic_words:
        return 0.5
    overlap = len(topic_words & set(snippet.lower().split()))
    return min(overlap / len(topic_words), 1.0)


def deduplicate(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for s in sources:
        url = s.get("url", "")
        if url not in seen:
            seen.add(url)
            out.append(s)
    return out


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def research(roadmap: dict[str, Any]) -> dict[str, Any]:
    api_key = config.serp_api_key()
    if not api_key:
        log.warning("No SerpAPI key — using knowledge-only mode")
        return {
            "topic_data": roadmap,
            "sources": [],
            "raw_snippets": "",
            "knowledge_only": True,
            "fetched_at": _now_iso(),
            "query_count": 0,
        }

    topic = str(roadmap.get("topic", ""))
    queries = [
        str(roadmap.get("research_query") or topic),
        f"DPDPA India {topic} SMB business compliance",
    ]
    collected: list[dict[str, Any]] = []
    for i, query in enumerate(queries):
        if i > 0:
            _sleep(QUERY_DELAY_SECONDS)
        for s in extract_snippets(search_serp(query, api_key)):
            s["relevance_score"] = score_relevance(s["snippet"], topic)
            s["query"] = query
            collected.append(s)

    sources = sorted(deduplicate(collected), key=lambda s: s["relevance_score"], reverse=True)
    raw_snippets = "\n\n".join(f"[{s['title']}] ({s['url']})\n{s['snippet']}" for s in sources[:8])
    log.info("Research complete: %s sources", len(sources))
    return {
        "topic_data": roadmap,
        "sources": sources,
        "raw_snippets": raw_snippets,
        "knowledge_only": False,
        "fetched_at": _now_iso(),
        "query_count": len(queries),
    }
