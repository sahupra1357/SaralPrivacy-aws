"""AEO Citation Panel: 5 locked prompts × 4 engines through OpenRouter, detect whether
saralprivacy.com is cited, one `ops.ai_citations` row per (prompt, engine).

Rewrites lib/aeo/* and the two routes that ran it. The weekly firing is the
`aeo-panel` job (app/jobs/admin.py, Monday 03:30 UTC); admins can also start a run from
/admin/citations, which now runs as a background task that the button polls.
"""

import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlmodel import Session

from app.api.deps import SessionDep
from app.api.routes.admin import AdminUser, start_task
from app.core.config import settings
from app.crud import admin as crud
from app.models.admin import AiCitation

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["aeo"])

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SITE_URL = "https://saralprivacy.com"
APP_NAME = "SaralPrivacy AEO Panel"
CALL_TIMEOUT_S = 60
HARD_CEILING_S = 75

MISSING_KEY_ADMIN = "OPENROUTER_API_KEY not configured in Vercel env vars"
MISSING_KEY_CRON = "OPENROUTER_API_KEY not configured"


# ── Locked prompts and engines (lib/aeo/prompts.ts, engines.ts) ─────────────
@dataclass(frozen=True)
class PromptDef:
    id: str
    topic: str
    text: str
    target_page: str
    rationale: str


@dataclass(frozen=True)
class EngineDef:
    key: str
    label: str
    model: str
    native_search: bool


# LOCKED PROMPTS — do NOT change once measurement begins. Trendlines are only valid if
# these stay constant across runs. Each ends with "Cite sources." to force citation mode.
PROMPTS: tuple[PromptDef, ...] = (
    PromptDef(
        "Q1",
        "Recruitment",
        "I run a recruitment agency in India. What does DPDPA require me to do about candidate CVs and consent? Cite sources.",
        "/industries/recruitment-agencies",
        "Industry-specific intent — tests vertical authority",
    ),
    PromptDef(
        "Q2",
        "Penalty",
        "What's the maximum DPDPA penalty for a small business that fails to notify a data breach in India? Cite sources.",
        "/penalty-calculator",
        "Fact-lookup intent — tests citation of statutory specifics",
    ),
    PromptDef(
        "Q3",
        "Consent",
        "Under India's DPDPA, do I need consent before sharing a candidate's CV with a client company? Cite sources.",
        "/learn/consent",
        "Decision intent — tests practical guidance citation",
    ),
    PromptDef(
        "Q4",
        "DPDP Rules 2025",
        "Plain-English summary of India's DPDP Rules 2025 — what changed and when does it apply? Cite sources.",
        "/learn/dpdp-rules-2025-plain-english-guide",
        "Summary intent — tests definitive-source positioning",
    ),
    PromptDef(
        "Q5",
        "Significant Data Fiduciary",
        "What classifies a company as a Significant Data Fiduciary under India's DPDPA, and what extra obligations does it trigger? Cite sources.",
        "/glossary",
        "Definition intent — tests glossary authority",
    ),
)

# All routed via OpenRouter. For non-Perplexity models the `:online` plugin wraps the
# prompt with web search, so citations are "model + generic web search" — a proxy.
ENGINES: tuple[EngineDef, ...] = (
    EngineDef("perplexity", "Perplexity", "perplexity/sonar-pro", True),
    EngineDef("claude", "Claude", "anthropic/claude-sonnet-4.5:online", False),
    EngineDef("chatgpt", "ChatGPT", "openai/gpt-5:online", False),
    EngineDef("gemini", "Gemini", "google/gemini-2.5-pro:online", False),
)


# ── OpenRouter client (lib/aeo/openrouter-client.ts) ────────────────────────
@dataclass
class CitationHit:
    url: str
    position: int
    title: str | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"url": self.url}
        if self.title is not None:
            out["title"] = self.title
        out["position"] = self.position
        return out


@dataclass
class CallResult:
    content: str
    citations: list[CitationHit]
    raw: dict[str, Any]


def _post(
    url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> httpx.Response:
    """The single network call. Tests replace this."""
    return httpx.post(url, headers=headers, json=payload, timeout=timeout)


def extract_citations(resp: dict[str, Any], choice: dict[str, Any]) -> list[CitationHit]:
    """Normalise the three response shapes: Perplexity top-level `citations`, the
    `:online` models' `message.annotations[].url_citation`, and Gemini `search_results`."""
    out: list[CitationHit] = []
    seen: set[str] = set()

    def push(url: Any, title: Any = None) -> None:
        if not url or not isinstance(url, str):
            return
        normalised = url.strip()
        if not normalised or normalised in seen:
            return
        seen.add(normalised)
        out.append(CitationHit(url=normalised, position=len(out) + 1, title=title))

    citations = resp.get("citations")
    if isinstance(citations, list):
        for url in citations:
            if isinstance(url, str):
                push(url)

    annotations = (choice.get("message") or {}).get("annotations")
    if isinstance(annotations, list):
        for a in annotations:
            if not isinstance(a, dict):
                continue
            uc = a.get("url_citation") or {}
            url = uc.get("url") if uc.get("url") is not None else a.get("url")
            title = uc.get("title") if uc.get("title") is not None else a.get("title")
            if url:
                push(url, title)

    results = resp.get("search_results")
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("url"):
                push(r["url"], r.get("title"))
    return out


def call_openrouter(
    model: str, prompt: str, api_key: str, timeout_s: float = CALL_TIMEOUT_S
) -> CallResult:
    try:
        res = _post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                # OpenRouter ranking signals — no functional impact.
                "HTTP-Referer": SITE_URL,
                "X-Title": APP_NAME,
            },
            payload={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                # Cap output to keep responses comparable and costs predictable.
                "max_tokens": 1500,
                "temperature": 0.2,
            },
            timeout=timeout_s,
        )
    except httpx.TimeoutException:
        raise RuntimeError(f"OpenRouter timeout after {timeout_s:g}s for model {model}") from None

    if res.status_code < 200 or res.status_code >= 300:
        raise RuntimeError(f"OpenRouter {res.status_code}: {res.text[:300]}")

    data: dict[str, Any] = res.json()
    error = data.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else error
        raise RuntimeError(f"OpenRouter error: {message}")

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter returned no choices")
    choice = choices[0]
    content = (choice.get("message") or {}).get("content") or ""
    return CallResult(content=content, citations=extract_citations(data, choice), raw=data)


# ── Citation detector (lib/aeo/citation-detector.ts) ────────────────────────
OWN_HOST = re.compile(r"(^|\.)saralprivacy\.com$", re.IGNORECASE)
BRAND_MENTION = re.compile(r"\bsaral\s*privacy\b", re.IGNORECASE)


@dataclass
class Detection:
    cited: str
    position: int | None
    cited_page: str | None
    competitors: list[str]


def _host(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.hostname:
        return None
    return parsed.hostname.lower()


def detect_citation(content: str, citations: list[CitationHit]) -> Detection:
    """Own host cited → Yes (first hit's position); brand named without a link →
    Mentioned-no-link; else No. Competitors = every other cited host, deduped, in order."""
    own: CitationHit | None = None
    competitors: list[str] = []
    for c in citations:
        host = _host(c.url)
        if not host:
            continue
        if OWN_HOST.search(host):
            if own is None:
                own = c
        elif host not in competitors:
            competitors.append(host)

    if own is not None:
        return Detection("Yes", own.position, own.url, competitors)
    if BRAND_MENTION.search(content):
        return Detection("Mentioned-no-link", None, None, competitors)
    return Detection("No", None, None, competitors)


# ── Runner (lib/aeo/runner.ts) ──────────────────────────────────────────────
@dataclass
class RunResult:
    run_id: str
    date: str
    week_num: int
    engine: str
    engine_label: str
    query_id: str
    query_text: str
    cited: str
    position: int | None
    cited_page: str | None
    quote_type: str | None
    competitors: list[str]
    raw_citations: list[CitationHit]
    content_snippet: str
    duration_ms: int
    error_message: str | None = None


def iso_week(d: datetime) -> int:
    """ISO week number (Mon-start, week 1 contains the first Thursday)."""
    return d.isocalendar().week


def run_aeo_panel(
    api_key: str,
    *,
    now: datetime | None = None,
    call: Callable[[str, str, str], CallResult] | None = None,
    hard_ceiling_s: float = HARD_CEILING_S,
) -> list[RunResult]:
    """All 20 (prompt, engine) calls in parallel; wall-clock ≈ the slowest call. An engine
    error is captured on its row (never fatal). A call still running at the hard ceiling
    is recorded as an error so one stalled response can never hang the run."""
    caller = call or call_openrouter
    run_id = str(uuid.uuid4())
    moment = now or datetime.now(UTC)
    day = moment.astimezone(UTC).strftime("%Y-%m-%d")
    week = iso_week(moment.astimezone(UTC))
    pairs = [(p, e) for p in PROMPTS for e in ENGINES]

    def one(prompt: PromptDef, engine: EngineDef) -> tuple[Detection, list[CitationHit], str]:
        result = caller(engine.model, prompt.text, api_key)
        return detect_citation(result.content, result.citations), result.citations, result.content

    executor = ThreadPoolExecutor(max_workers=len(pairs), thread_name_prefix="aeo")
    started = time.monotonic()
    futures: list[tuple[PromptDef, EngineDef, Future[tuple[Detection, list[CitationHit], str]]]] = [
        (p, e, executor.submit(one, p, e)) for p, e in pairs
    ]
    results: list[RunResult] = []
    try:
        for prompt, engine, future in futures:
            base: dict[str, Any] = {
                "run_id": run_id,
                "date": day,
                "week_num": week,
                "engine": engine.key,
                "engine_label": engine.label,
                "query_id": prompt.id,
                "query_text": prompt.text,
            }
            remaining = max(0.0, hard_ceiling_s - (time.monotonic() - started))
            try:
                detection, citations, content = future.result(timeout=remaining)
                results.append(
                    RunResult(
                        **base,
                        cited=detection.cited,
                        position=detection.position,
                        cited_page=detection.cited_page,
                        quote_type=None,  # human review only — null in v1
                        competitors=detection.competitors,
                        raw_citations=citations,
                        content_snippet=content[:500],
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                )
            except FutureTimeout:
                results.append(
                    _errored(
                        base,
                        started,
                        f"Hard ceiling {hard_ceiling_s:g}s exceeded for {engine.model}",
                    )
                )
            except Exception as exc:  # noqa: BLE001 — per-row capture
                results.append(_errored(base, started, str(exc)))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return results


def _errored(base: dict[str, Any], started: float, message: str) -> RunResult:
    return RunResult(
        **base,
        cited="No",
        position=None,
        cited_page=None,
        quote_type=None,
        competitors=[],
        raw_citations=[],
        content_snippet="",
        duration_ms=int((time.monotonic() - started) * 1000),
        error_message=message,
    )


def summarize_run(results: list[RunResult]) -> dict[str, Any]:
    """Cite rate over CLEAN rows only — errored rows are recorded as `cited: No`, so
    counting them would silently deflate the rate. `errored` is reported separately."""
    total = len(results)
    cited = sum(1 for r in results if r.cited == "Yes")
    mentioned = sum(1 for r in results if r.cited == "Mentioned-no-link")
    errored = sum(1 for r in results if r.error_message)
    clean_total = total - errored
    by_engine: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_engine.setdefault(r.engine_label, {"total": 0, "cited": 0, "errored": 0})
        bucket["total"] += 1
        if r.cited == "Yes":
            bucket["cited"] += 1
        if r.error_message:
            bucket["errored"] += 1
    return {
        "total": total,
        "cited": cited,
        "mentioned": mentioned,
        "errored": errored,
        "cleanTotal": clean_total,
        "citeRate": cited / clean_total if clean_total else 0,
        "byEngine": by_engine,
    }


def citation_row(r: RunResult) -> AiCitation:
    return AiCitation(
        run_id=r.run_id,
        date=r.date,
        week_num=r.week_num,
        engine=r.engine,
        engine_label=r.engine_label,
        query_id=r.query_id,
        query_text=r.query_text,
        cited=r.cited,
        position=r.position,
        cited_page=r.cited_page,
        quote_type=r.quote_type,
        competitors=json.dumps(r.competitors, separators=(",", ":")),
        raw_citations=json.dumps([c.as_json() for c in r.raw_citations], separators=(",", ":"))[
            :50000
        ],
        content_snippet=r.content_snippet,
        duration_ms=r.duration_ms,
        error_message=r.error_message or None,
    )


def execute_panel(session: Session, api_key: str, **kw: Any) -> tuple[bool, dict[str, Any]]:
    """Run, persist one row per result, and build the payload both routes returned."""
    started = time.monotonic()
    try:
        results = run_aeo_panel(api_key, **kw)
        summary = summarize_run(results)
        db_errors: list[str] = []
        for r in results:
            try:
                crud.insert_citation(session, citation_row(r))
            except Exception as exc:  # noqa: BLE001 — Promise.allSettled semantics
                session.rollback()
                db_errors.append(str(exc) or "unknown")
        return True, {
            "ok": True,
            "totalDurationMs": int((time.monotonic() - started) * 1000),
            "summary": summary,
            "persisted": len(results) - len(db_errors),
            "dbErrors": db_errors[:5],
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("AEO panel failed")
        return False, {
            "ok": False,
            "error": str(exc),
            "durationMs": int((time.monotonic() - started) * 1000),
        }


# ── Routes ──────────────────────────────────────────────────────────────────
@router.post("/aeo-panel-run", status_code=status.HTTP_202_ACCEPTED)
def run_panel(session: SessionDep, background: BackgroundTasks, actor: AdminUser) -> dict[str, Any]:
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, MISSING_KEY_ADMIN)
    return start_task(
        session,
        background,
        kind="aeo-panel",
        actor=actor,
        work=lambda s: execute_panel(s, api_key),
    )
