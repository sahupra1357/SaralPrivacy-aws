"""Setu retrieval — Plane 1 backbone (rewrite of frontend/lib/chat/{retrieve,pinecone}.ts).

Two retrievers, fused:

* **Pinecone** is the primary store (decision D7). The index is *integrated*, so we send
  plain text and Pinecone embeds it server-side; the REST API is called with httpx and no
  SDK, exactly as the TypeScript called it with `fetch`. Every failure returns ``None`` so
  the caller silently degrades.
* **BM25** over ``app/data/chat-index.json`` is deterministic, offline and free. It nails
  statutory vocabulary ("Section 33", "SDF") that semantic search blurs, and it is the
  fallback whenever Pinecone is unset or unreachable.

Neither wins alone, so ``fuse_retrieval`` merges them by Reciprocal Rank Fusion and then
applies the router-aware boosts (industry, current page, tier) both paths have always had.

``app/data/chat-index.json`` is a copy of ``frontend/public/chat-index.json``, produced by
``frontend/scripts/build-chat-index.mts`` and refreshed by
``frontend/scripts/export-chat-kb.mjs``. Nothing here re-authors corpus content.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
INDEX_PATH = DATA_DIR / "chat-index.json"

Confidence = Literal["high", "low"]


# ── Corpus types ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChatChunk:
    id: str
    url: str
    title: str
    tier: int
    topic_tags: list[str]
    section: str
    text: str
    extraction: str = "typed"
    industry: str | None = None

    @staticmethod
    def from_json(raw: dict[str, Any]) -> ChatChunk:
        return ChatChunk(
            id=str(raw.get("id", "")),
            url=str(raw.get("url", "")),
            title=str(raw.get("title", "")),
            tier=int(raw.get("tier", 1)),
            topic_tags=[str(t) for t in raw.get("topicTags", [])],
            section=str(raw.get("section", "")),
            text=str(raw.get("text", "")),
            extraction="tsx-text" if raw.get("extraction") == "tsx-text" else "typed",
            industry=str(raw["industry"]) if raw.get("industry") else None,
        )


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: ChatChunk
    score: float


@dataclass
class RetrievalResult:
    hits: list[RetrievedChunk] = field(default_factory=list)
    confidence: Confidence = "low"
    matched_term_ratio: float = 0.0


# ── Tokenisation ──────────────────────────────────────────────────────────────
STOPWORDS = frozenset(
    (
        "a an and about also are as at be by can do does for from has have how i in is it "
        "its just me my of on or our please pls so that the this to under us we what when "
        "where which who will with you your"
    ).split(" ")
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def stem(word: str) -> str:
    """Light stemmer: folds plural/verb/nominal suffixes so "deletion", "deleted" and
    "delete" share a stem. Deliberately crude — the golden set is the judge.

    Each length guard is re-evaluated against the *current* value, exactly as the
    chained ``String.replace`` calls in retrieve.ts did.
    """
    w = word
    if len(w) > 5:
        w = re.sub(r"(ation|ations)$", "ate", w, count=1)
    if len(w) > 5:
        w = re.sub(r"(ion|ions)$", "", w, count=1)
    if len(w) > 4:
        w = re.sub(r"(ies)$", "y", w, count=1)
    if len(w) > 4:
        w = re.sub(r"(ing|ed|es)$", "", w, count=1)
    if len(w) > 3:
        w = re.sub(r"(s)$", "", w, count=1)
    if len(w) > 3:
        w = re.sub(r"(e|y)$", "", w, count=1)
    return w


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in _NON_ALNUM.split(text.lower()) if len(t) > 1 and t not in STOPWORDS]


# ── BM25 ──────────────────────────────────────────────────────────────────────
K1 = 1.5
B = 0.75
CONFIDENCE_MIN_RATIO = 0.4  # share of query terms the top hit must cover
CONFIDENCE_MIN_SCORE = 3.0  # absolute BM25 floor for the top hit

RRF_K = 60  # standard Reciprocal Rank Fusion damping


class Bm25Engine:
    """Postings built once per index file and cached for the process lifetime."""

    def __init__(
        self, chunks: list[ChatChunk], embeddings: list[list[float]] | None = None
    ) -> None:
        self.chunks = chunks
        self.embeddings = embeddings
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.doc_len: list[int] = []
        for i, c in enumerate(chunks):
            # Field weighting: title + tags count 3x, section 2x, body 1x — a match on
            # what a page IS about must outrank incidental body mentions.
            head = f"{c.title} {' '.join(c.topic_tags)}"
            terms = tokenize(f"{head} {head} {head} {c.section} {c.section} {c.text}")
            self.doc_len.append(len(terms))
            tf: dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            for term, count in tf.items():
                self.postings.setdefault(term, []).append((i, count))
        self.avg_len = sum(self.doc_len) / max(1, len(self.doc_len))

    def score(self, query_terms: list[str]) -> tuple[list[float], list[int]]:
        n = len(self.chunks)
        scores = [0.0] * n
        matched = [0] * n
        for term in set(query_terms):
            postings = self.postings.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for chunk_idx, tf in postings:
                norm = tf * (K1 + 1)
                denom = tf + K1 * (1 - B + (B * self.doc_len[chunk_idx]) / self.avg_len)
                scores[chunk_idx] += idf * (norm / denom)
                matched[chunk_idx] += 1
        return scores, matched


@lru_cache(maxsize=4)
def load_index(index_path: str | None = None) -> Bm25Engine:
    path = Path(index_path) if index_path else INDEX_PATH
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks = [ChatChunk.from_json(c) for c in raw.get("chunks", [])]
    embeddings = raw.get("embeddings")
    return Bm25Engine(chunks, embeddings)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    return dot / ((math.sqrt(na) * math.sqrt(nb)) or 1)


def boost_factor(
    chunk: ChatChunk, industry: str | None = None, page_url: str | None = None
) -> float:
    """Router-aware boost applied to any retrieval result, whatever its source, so
    Pinecone hits get the same industry/tier/page weighting the lexical path has."""
    boost = 1.0
    if industry and chunk.industry == industry:
        boost *= 1.35
    if industry and chunk.industry and chunk.industry != industry:
        boost *= 0.6
    if page_url and chunk.url == page_url:
        boost *= 1.15
    if chunk.tier == 1:
        boost *= 1.12
    # Conflict rule (spec §6.1): Tier 1 beats Tier 4 — dated briefings may inform
    # freshness turns but must never outrank canonical Learn content.
    if chunk.tier == 4:
        boost *= 0.6
    if chunk.extraction == "tsx-text":
        boost *= 0.92
    return boost


def platform_tour(index_path: str | None = None, top_k: int = 6) -> RetrievalResult:
    """Deterministic platform-tour grounding: the platform-guide chunks, overview first.
    Used when the user asks ABOUT the platform in words that share no vocabulary with any
    content chunk ("help me navigate this site")."""
    engine = load_index(index_path)
    tools = [c for c in engine.chunks if c.id.startswith("tool:")]
    ordered = [c for c in tools if c.id == "tool:platform-overview"]
    ordered += [c for c in tools if c.id != "tool:platform-overview"]
    return RetrievalResult(
        hits=[RetrievedChunk(chunk=c, score=10.0) for c in ordered[:top_k]],
        confidence="high",
        matched_term_ratio=1.0,
    )


def retrieve(
    query: str,
    *,
    top_k: int = 6,
    industry: str | None = None,
    page_url: str | None = None,
    query_vector: list[float] | None = None,
    index_path: str | None = None,
) -> RetrievalResult:
    engine = load_index(index_path)
    chunks = engine.chunks
    query_terms = tokenize(query)
    if not query_terms:
        return RetrievalResult(hits=[], confidence="low", matched_term_ratio=0.0)

    scores, matched = engine.score(query_terms)

    # Optional dense merge — min-max normalise both signals first.
    final = scores
    if query_vector and engine.embeddings:
        max_lex = max([*scores, 1e-9])
        final = [
            0.45 * (scores[i] / max_lex) * 10
            + 0.55 * max(0.0, _cosine(query_vector, engine.embeddings[i])) * 10
            for i in range(len(chunks))
        ]

    # Router-aware boosts (multiplicative; never resurrect zero-match chunks).
    boosted = [
        0.0 if s == 0 else s * boost_factor(chunks[i], industry, page_url)
        for i, s in enumerate(final)
    ]

    order = sorted(
        ((score, i) for i, score in enumerate(boosted) if score > 0),
        key=lambda x: x[0],
        reverse=True,
    )[:top_k]

    hits = [RetrievedChunk(chunk=chunks[i], score=score) for score, i in order]
    matched_term_ratio = (matched[order[0][1]] / len(set(query_terms))) if order else 0.0
    confidence: Confidence = (
        "high"
        if order
        and matched_term_ratio >= CONFIDENCE_MIN_RATIO
        and scores[order[0][1]] >= CONFIDENCE_MIN_SCORE
        else "low"
    )
    return RetrievalResult(hits=hits, confidence=confidence, matched_term_ratio=matched_term_ratio)


def fuse_retrieval(
    vector: RetrievalResult | None,
    lexical: RetrievalResult,
    *,
    industry: str | None = None,
    page_url: str | None = None,
    top_k: int = 6,
) -> RetrievalResult:
    """Fuse Pinecone semantic hits with local lexical hits by Reciprocal Rank Fusion,
    then apply the router boosts. Semantic leads (it is the primary store); lexical
    corroborates."""
    if vector is None or not vector.hits:
        return lexical

    by_id: dict[str, tuple[ChatChunk, float]] = {}

    def add_ranked(hits: list[RetrievedChunk], weight: float) -> None:
        for rank, h in enumerate(hits):
            contribution = weight / (RRF_K + rank + 1)
            prev = by_id.get(h.chunk.id)
            by_id[h.chunk.id] = (h.chunk, (prev[1] if prev else 0.0) + contribution)

    add_ranked(vector.hits, 1.0)
    add_ranked(lexical.hits, 0.75)

    fused = sorted(
        (
            RetrievedChunk(chunk=chunk, score=rrf * boost_factor(chunk, industry, page_url))
            for chunk, rrf in by_id.values()
        ),
        key=lambda h: h.score,
        reverse=True,
    )[:top_k]

    # Either retriever being confident is enough — they fail on different things.
    confidence: Confidence = "high" if "high" in (vector.confidence, lexical.confidence) else "low"
    return RetrievalResult(
        hits=fused,
        confidence=confidence,
        matched_term_ratio=max(vector.matched_term_ratio, lexical.matched_term_ratio),
    )


# ── Pinecone (REST, no SDK) ───────────────────────────────────────────────────
INDEX_NAME = "saralprivacy-setu"
INDEX_HOST = "saralprivacy-setu-k0kthbf.svc.aped-4627-b74a.pinecone.io"
NAMESPACE = "content"
EMBED_MODEL = "llama-text-embed-v2"
RERANK_MODEL = "bge-reranker-v2-m3"
API_VERSION = "2025-04"

#: Rerank scores are 0..1 relevance. Below this the top hit is not really about the
#: question — treat it as "not on the site" (the refusal floor).
PINECONE_CONFIDENCE_FLOOR = 0.12


def pinecone_key() -> str | None:
    key = settings.PINECONE_API_KEY.strip()
    return key if len(key) > 10 else None


def is_pinecone_configured() -> bool:
    return pinecone_key() is not None


def _headers(key: str, content_type: str = "application/json") -> dict[str, str]:
    return {"Api-Key": key, "Content-Type": content_type, "X-Pinecone-API-Version": API_VERSION}


def pinecone_search(
    query: str,
    *,
    top_k: int = 24,
    top_n: int = 6,
    industry: str | None = None,
    timeout: float = 4.0,
) -> RetrievalResult | None:
    """Semantic search + rerank. Returns ``None`` on ANY failure so callers fall back."""
    key = pinecone_key()
    if not key or not query.strip():
        return None

    # Sector questions must still see cross-cutting law pages, so filter by
    # "this industry OR not industry-specific" rather than industry alone.
    body: dict[str, Any] = {
        "query": {"top_k": top_k, "inputs": {"text": query[:2000]}},
        "rerank": {"model": RERANK_MODEL, "top_n": top_n, "rank_fields": ["chunk_text"]},
    }
    if industry:
        body["query"]["filter"] = {
            "$or": [{"industry": {"$eq": industry}}, {"industry": {"$exists": False}}]
        }

    try:
        res = httpx.post(
            f"https://{INDEX_HOST}/records/namespaces/{NAMESPACE}/search",
            headers=_headers(key),
            json=body,
            timeout=timeout,
        )
        if res.status_code >= 400:
            log.error("pinecone search %s: %s", res.status_code, res.text[:200])
            return None
        hits = res.json().get("result", {}).get("hits", [])
    except (httpx.HTTPError, ValueError) as e:
        log.error("pinecone search failed: %s", e)
        return None

    if not hits:
        return None

    retrieved: list[RetrievedChunk] = []
    for h in hits:
        f = h.get("fields") or {}
        tags = f.get("topicTags")
        retrieved.append(
            RetrievedChunk(
                chunk=ChatChunk(
                    id=str(h.get("_id") or h.get("id") or ""),
                    url=str(f.get("url") or ""),
                    title=str(f.get("title") or ""),
                    section=str(f.get("section") or ""),
                    tier=int(f.get("tier") or 1),
                    topic_tags=[str(t) for t in tags] if isinstance(tags, list) else [],
                    text=str(f.get("chunk_text") or ""),
                    extraction="tsx-text" if f.get("extraction") == "tsx-text" else "typed",
                    industry=str(f["industry"]) if f.get("industry") else None,
                ),
                score=float(h.get("_score") or h.get("score") or 0),
            )
        )

    top = retrieved[0].score if retrieved else 0.0
    return RetrievalResult(
        hits=retrieved,
        confidence="high" if top >= PINECONE_CONFIDENCE_FLOOR else "low",
        matched_term_ratio=top,
    )


def pinecone_stats(timeout: float = 4.0) -> dict[str, int] | None:
    """``{"vectorCount": n}`` for the content namespace, or ``None`` when unreachable."""
    key = pinecone_key()
    if not key:
        return None
    try:
        res = httpx.post(
            f"https://{INDEX_HOST}/describe_index_stats",
            headers=_headers(key),
            content=b"{}",
            timeout=timeout,
        )
        if res.status_code >= 400:
            return None
        data = res.json()
    except (httpx.HTTPError, ValueError):
        return None
    ns = (data.get("namespaces") or {}).get(NAMESPACE) or {}
    count = ns.get("recordCount") or ns.get("vectorCount") or data.get("totalVectorCount") or 0
    return {"vectorCount": int(count)}
