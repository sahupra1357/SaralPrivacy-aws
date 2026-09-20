"""Live briefings context (Plane 3). Rewrite of ``frontend/lib/chat/briefings-live.ts``.

On a freshness-intent turn the route fetches the most recent briefings, scores them
lexically against the question, and injects the top matches as a DATED context block. The
tier rule stands: dated updates only — the prompt still prefers canonical Learn content for
settled law. Any failure (missing table, schema drift, DB error) degrades to ``None`` and
the turn proceeds on the static index alone.

``app.briefings_meta`` belongs to the **editorial** module, so this reads it with a plain
SELECT rather than importing that module's model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.services.retrieval import tokenize

log = logging.getLogger(__name__)

FRESH_INTENT_RE = re.compile(
    r"\b(latest|this (week|month)|recent(ly)?|what('s| is| has) (new|changed|happening)|"
    r"any (news|updates?)|update on|enforcement (news|date)|notified)\b"
)

# Field precedence copied verbatim from briefings-live.ts. Keys that the Postgres table
# does not have (the camelCase Appwrite-era spellings) simply never match, exactly as they
# never matched through the Supabase adapter.
_TITLE_KEYS = ("title", "headline")
_SUMMARY_KEYS = (
    "summary",
    "excerpt",
    "whyItMatters",
    "business_impact",
    "businessImpact",
    "content",
)
_DATE_KEYS = ("date", "published_at", "publishedAt")

_SELECT_RECENT = text(
    """
    select title, summary, excerpt, why_it_matters, published_at, created_at
    from app.briefings_meta
    order by created_at desc
    limit 25
    """
)


@dataclass(frozen=True)
class LiveBriefing:
    title: str
    date: str
    summary: str


def _pick(doc: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        v = doc.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def fetch_live_briefings(session: Session, query: str, limit: int = 3) -> list[LiveBriefing] | None:
    """Best-effort fetch of recent briefings matching the question. ``None`` on any failure."""
    try:
        # SAVEPOINT: a missing table or schema drift must not poison the caller's
        # transaction — the turn has to continue on the static index alone.
        with session.begin_nested():
            rows = session.execute(_SELECT_RECENT).mappings().all()
    except SQLAlchemyError as e:
        log.warning("live briefings unavailable: %s", e)
        return None

    q_tokens = set(tokenize(query))
    scored: list[tuple[LiveBriefing, int]] = []
    for row in rows:
        doc = dict(row)
        title = _pick(doc, _TITLE_KEYS)
        summary = _pick(doc, _SUMMARY_KEYS)
        created = doc.get("created_at")
        date = _pick(doc, _DATE_KEYS) or (str(created)[:10] if created else "")
        if not title:
            continue
        overlap = sum(1 for t in tokenize(f"{title} {summary}") if t in q_tokens)
        scored.append((LiveBriefing(title=title, date=date, summary=summary[:600]), overlap))

    # Highest overlap first, then newest date first — the same two-key comparator the
    # TypeScript used.
    scored.sort(key=lambda x: (x[1], x[0].date), reverse=True)
    top = [b for b, _ in scored[:limit]]
    return top or None


def briefings_context_block(items: list[LiveBriefing]) -> str:
    lines = "\n\n".join(f"[briefing · {b.date}] {b.title}\n{b.summary}" for b in items)
    return (
        '<briefings_context note="dated updates from SaralPrivacy Daily Briefings — cite '
        'the date when you use one; for settled law prefer the Learn guides">\n'
        f"{lines}\n</briefings_context>"
    )
