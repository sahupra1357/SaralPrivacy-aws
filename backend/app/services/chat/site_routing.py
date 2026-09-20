"""Setu site router — the ONLY source of link targets for the bot (spec §3, v2.4).

Every citation and action URL that reaches the client must pass :func:`is_valid_citation`;
the agent never generates a URL, it selects from ``ROUTES``.

The route table itself is still authored in ``frontend/lib/chat/site-routing.ts`` — that
file is the one a dead-link test can resolve against the Next.js ``app/`` directory, and
the widget imports ``chipsForPage`` from it. ``frontend/scripts/export-chat-kb.mjs``
serialises the data half into ``app/data/site-routes.json``, which this module reads, so
there is exactly one place a URL is ever written down.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "site-routes.json"

CITATION_HOST = "https://saralprivacy.com"

Intent = str  # "assessment" | "penalty" | "discovery" | "notice" | "whitepaper"

INTENT_TOOL_URL: dict[str, str] = {
    "assessment": "/assessment",
    "penalty": "/penalty-calculator",
    "discovery": "/discovery",
    "notice": "/tools/dpdpa-privacy-notice-generator",
    "whitepaper": "/white-paper",
}


@dataclass(frozen=True)
class Route:
    url: str
    title: str
    tier: int
    topic_tags: tuple[str, ...]
    triggers: tuple[str, ...]
    summary: str
    industry: str | None = None


@dataclass(frozen=True)
class RouteTable:
    routes: tuple[Route, ...]
    exclude_from_authority: tuple[str, ...]
    never_surface_prefixes: tuple[str, ...]
    industry_slugs: tuple[str, ...]
    linkable_paths: frozenset[str]


@lru_cache(maxsize=1)
def _table() -> RouteTable:
    raw: dict[str, Any] = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    routes = tuple(
        Route(
            url=str(r["url"]),
            title=str(r["title"]),
            tier=int(r["tier"]),
            topic_tags=tuple(str(t) for t in r.get("topicTags", [])),
            triggers=tuple(str(t) for t in r.get("triggers", [])),
            summary=str(r.get("summary", "")),
            industry=str(r["industry"]) if r.get("industry") else None,
        )
        for r in raw["routes"]
    )
    exclude = tuple(str(u) for u in raw["excludeFromAuthority"])
    return RouteTable(
        routes=routes,
        exclude_from_authority=exclude,
        never_surface_prefixes=tuple(str(p) for p in raw["neverSurfacePrefixes"]),
        industry_slugs=tuple(str(s) for s in raw["industrySlugs"]),
        linkable_paths=frozenset([*(r.url for r in routes), *exclude]),
    )


def routes() -> tuple[Route, ...]:
    return _table().routes


def industry_slugs() -> tuple[str, ...]:
    return _table().industry_slugs


def exclude_from_authority() -> tuple[str, ...]:
    return _table().exclude_from_authority


def route_by_url(url: str) -> Route | None:
    return next((r for r in routes() if r.url == url), None)


def normalize_path(url: str) -> str | None:
    path = url.strip()
    if path.startswith(CITATION_HOST):
        path = path[len(CITATION_HOST) :]
    if path == "":
        path = "/"
    if not path.startswith("/"):
        return None  # other domains / protocols / garbage
    # Citations must be canonical: no query, no hash, no trailing slash.
    if "?" in path or "#" in path:
        return None
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def is_never_surfaced(path: str) -> bool:
    """Prefixes ending in "/" ban only subpaths (the bare hub stays linkable)."""
    return any(
        path.startswith(p) if p.endswith("/") else (path == p or path.startswith(p + "/"))
        for p in _table().never_surface_prefixes
    )


def is_valid_citation(url: str) -> bool:
    """True when the URL may be emitted by the bot (citation or action)."""
    path = normalize_path(url)
    if path is None or is_never_surfaced(path):
        return False
    return path in _table().linkable_paths


def is_authority_citation(url: str) -> bool:
    """True when the URL may ground a substantive claim (Tier 1 or 2)."""
    path = normalize_path(url)
    if path is None:
        return False
    route = route_by_url(path)
    return route is not None and route.tier in (1, 2)


def routes_for_topic(topic: str) -> list[Route]:
    needle = topic.strip().lower()
    if needle == "":
        return []
    matches = [
        r
        for r in routes()
        if any(needle in t or t in needle for t in r.topic_tags)
        or any(needle in t or t in needle for t in r.triggers)
    ]
    return sorted(matches, key=lambda r: r.tier)


def route_for_industry(slug: str) -> Route | None:
    return next((r for r in routes() if r.industry == slug), None)


def tool_for_intent(intent: Intent) -> Route | None:
    url = INTENT_TOOL_URL.get(intent)
    return route_by_url(url) if url else None
