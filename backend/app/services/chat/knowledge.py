"""Plane 2 — typed knowledge lookups (strategy §3.2). Rewrite of
``frontend/lib/chat/knowledge-tools.ts``.

MVP pair: glossary terms and checklist controls. Outputs are rendered from typed data the
model narrates verbatim — it never recalls these from memory.

The data is authored in ``frontend/components/glossary/glossaryData.ts`` and
``frontend/lib/data/compliance-checklist.ts`` (both also power the public /glossary and
/compliance-checklist pages) and exported to ``app/data/{glossary,checklist}.json`` by
``frontend/scripts/export-chat-kb.mjs``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.retrieval import tokenize

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

GLOSSARY_MATCH_FLOOR = 0.34


@dataclass(frozen=True)
class GlossaryTerm:
    id: str
    term: str
    section: str
    category: str
    definition: str
    related_ids: tuple[str, ...] = ()
    learn_href: str | None = None


@dataclass(frozen=True)
class GlossaryHit:
    term: str
    section: str
    category: str
    definition: str
    url: str = "/glossary"
    learn_href: str | None = None


@dataclass(frozen=True)
class GlossaryLookup:
    best: GlossaryHit | None
    related: list[GlossaryHit]


@dataclass(frozen=True)
class ChecklistHit:
    id: str
    part: int
    section_title: str
    requirement: str
    guardrail: str
    reference: str
    url: str = "/compliance-checklist"


@lru_cache(maxsize=1)
def _terms() -> tuple[GlossaryTerm, ...]:
    raw: list[dict[str, Any]] = json.loads((DATA_DIR / "glossary.json").read_text(encoding="utf-8"))
    return tuple(
        GlossaryTerm(
            id=str(t["id"]),
            term=str(t["term"]),
            section=str(t["section"]),
            category=str(t["category"]),
            definition=str(t["definition"]),
            related_ids=tuple(str(r) for r in t.get("relatedIds", [])),
            learn_href=str(t["learnHref"]) if t.get("learnHref") else None,
        )
        for t in raw
    )


@lru_cache(maxsize=1)
def _sections() -> tuple[dict[str, Any], ...]:
    raw: list[dict[str, Any]] = json.loads(
        (DATA_DIR / "checklist.json").read_text(encoding="utf-8")
    )
    return tuple(raw)


def _to_hit(t: GlossaryTerm) -> GlossaryHit:
    return GlossaryHit(
        term=t.term,
        section=t.section,
        category=t.category,
        definition=t.definition,
        learn_href=t.learn_href,
    )


def lookup_glossary(query: str) -> GlossaryLookup:
    """Exact-or-close glossary lookup. Returns best match plus near matches."""
    q = query.strip().lower()
    if not q:
        return GlossaryLookup(best=None, related=[])

    terms = _terms()
    exact = next((t for t in terms if t.term.lower() == q or t.id == q), None)
    if exact is not None:
        by_id = {t.id: t for t in terms}
        related = [_to_hit(by_id[rid]) for rid in exact.related_ids if rid in by_id][:3]
        return GlossaryLookup(best=_to_hit(exact), related=related)

    q_tokens = set(tokenize(q))
    scored: list[tuple[GlossaryTerm, float]] = []
    for t in terms:
        term_tokens = tokenize(t.term)
        overlap = sum(1 for tok in term_tokens if tok in q_tokens)
        lowered = t.term.lower()
        contains = 1 if (q in lowered or lowered in q) else 0
        score = overlap / max(1, len(term_tokens)) + contains
        if score > GLOSSARY_MATCH_FLOOR:
            scored.append((t, score))
    scored.sort(key=lambda x: x[1], reverse=True)  # stable, like Array.prototype.sort

    if not scored:
        return GlossaryLookup(best=None, related=[])
    return GlossaryLookup(best=_to_hit(scored[0][0]), related=[_to_hit(t) for t, _ in scored[1:4]])


def _item_hit(section: dict[str, Any], item: dict[str, Any]) -> ChecklistHit:
    return ChecklistHit(
        id=str(item["id"]),
        part=int(section["part"]),
        section_title=str(section["title"]),
        requirement=str(item["requirement"]),
        guardrail=str(item["guardrail"]),
        reference=str(item["reference"]),
    )


_ID_RE = re.compile(r"^\d{1,2}\.\d{1,2}$")


def lookup_checklist(query: str, limit: int = 5) -> list[ChecklistHit]:
    """Look up checklist controls by exact id ("4.3") or by topic keywords."""
    q = query.strip()
    if not q:
        return []

    if _ID_RE.match(q):
        for sec in _sections():
            item = next((it for it in sec["items"] if it["id"] == q), None)
            if item is not None:
                return [_item_hit(sec, item)]
        return []

    q_tokens = set(tokenize(q))
    if not q_tokens:
        return []
    scored: list[tuple[ChecklistHit, float]] = []
    for sec in _sections():
        sec_overlap = sum(1 for t in tokenize(str(sec["title"])) if t in q_tokens)
        for it in sec["items"]:
            item_tokens = tokenize(f"{it['subsection']} {it['requirement']} {it['guardrail']}")
            overlap = sum(1 for t in item_tokens if t in q_tokens)
            score = overlap + sec_overlap * 0.5
            if score > 0:
                scored.append((_item_hit(sec, it), score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [hit for hit, _ in scored[:limit]]
