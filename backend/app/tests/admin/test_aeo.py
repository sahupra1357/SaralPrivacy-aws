"""AEO Citation Panel: OpenRouter client, detector, runner, summary, admin route."""

import json
import threading
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import aeo
from app.core.config import settings
from app.models.admin import AdminTask, AiCitation

OWN = "https://saralprivacy.com/learn/consent"


def _response(status: int, body: Any) -> httpx.Response:
    content = body if isinstance(body, str) else json.dumps(body)
    return httpx.Response(
        status, content=content.encode(), request=httpx.Request("POST", aeo.OPENROUTER_URL)
    )


# ── OpenRouter client ─────────────────────────────────────────────────────
def test_call_openrouter_sends_the_panel_request_and_normalises_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_post(
        url: str, *, headers: dict[str, str], payload: dict[str, Any], timeout: float
    ) -> httpx.Response:
        seen.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return _response(
            200,
            {
                "citations": [OWN, "https://a.example/x", OWN],
                "choices": [
                    {
                        "message": {
                            "content": "Answer",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {"url": "https://b.example", "title": "B"},
                                },
                                {"url": "https://a.example/x"},
                            ],
                        }
                    }
                ],
                "search_results": [{"url": "https://c.example", "title": "C"}],
            },
        )

    monkeypatch.setattr(aeo, "_post", fake_post)
    result = aeo.call_openrouter("perplexity/sonar-pro", "Q?", "sk-test")

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-test"
    assert seen["headers"]["HTTP-Referer"] == "https://saralprivacy.com"
    assert seen["headers"]["X-Title"] == "SaralPrivacy AEO Panel"
    assert seen["payload"] == {
        "model": "perplexity/sonar-pro",
        "messages": [{"role": "user", "content": "Q?"}],
        "max_tokens": 1500,
        "temperature": 0.2,
    }
    assert seen["timeout"] == 60
    assert result.content == "Answer"
    assert [(c.url, c.position, c.title) for c in result.citations] == [
        (OWN, 1, None),
        ("https://a.example/x", 2, None),
        ("https://b.example", 3, "B"),
        ("https://c.example", 4, "C"),
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(502, "bad gateway"), "OpenRouter 502: bad gateway"),
        (_response(200, {"error": {"message": "quota"}}), "OpenRouter error: quota"),
        (_response(200, {"choices": []}), "OpenRouter returned no choices"),
    ],
)
def test_call_openrouter_errors(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, message: str
) -> None:
    monkeypatch.setattr(aeo, "_post", lambda *_a, **_k: response)
    with pytest.raises(RuntimeError, match=message):
        aeo.call_openrouter("m", "p", "k")


def test_call_openrouter_timeout_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*_a: Any, **_k: Any) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(aeo, "_post", slow)
    with pytest.raises(
        RuntimeError, match="OpenRouter timeout after 60s for model openai/gpt-5:online"
    ):
        aeo.call_openrouter("openai/gpt-5:online", "p", "k")


# ── Detector ──────────────────────────────────────────────────────────────
def test_detect_citation_yes_uses_first_own_hit_and_lists_competitors() -> None:
    hits = [
        aeo.CitationHit("https://dpo.example/a", 1),
        aeo.CitationHit("https://www.saralprivacy.com/glossary", 2),
        aeo.CitationHit(OWN, 3),
        aeo.CitationHit("https://dpo.example/b", 4),
        aeo.CitationHit("not a url", 5),
    ]
    d = aeo.detect_citation("text", hits)
    assert (d.cited, d.position, d.cited_page) == (
        "Yes",
        2,
        "https://www.saralprivacy.com/glossary",
    )
    assert d.competitors == ["dpo.example"]


def test_detect_citation_brand_mention_without_link() -> None:
    d = aeo.detect_citation(
        "See Saral Privacy for templates", [aeo.CitationHit("https://x.example", 1)]
    )
    assert (d.cited, d.position, d.cited_page, d.competitors) == (
        "Mentioned-no-link",
        None,
        None,
        ["x.example"],
    )


def test_detect_citation_no() -> None:
    assert aeo.detect_citation("nothing", []).cited == "No"
    # a look-alike host is a competitor, not us
    d = aeo.detect_citation("x", [aeo.CitationHit("https://notsaralprivacy.com/a", 1)])
    assert d.cited == "No" and d.competitors == ["notsaralprivacy.com"]


# ── Runner and summary ────────────────────────────────────────────────────
def test_run_panel_covers_5_prompts_by_4_engines_and_captures_errors() -> None:
    def fake_call(model: str, prompt: str, _key: str) -> aeo.CallResult:
        if model.startswith("google/"):
            raise RuntimeError("OpenRouter 503: down")
        cites = [aeo.CitationHit(OWN, 1)] if prompt.startswith("Under India") else []
        return aeo.CallResult(content="x" * 900, citations=cites, raw={})

    now = datetime(2026, 9, 14, 3, 30, tzinfo=UTC)
    results = aeo.run_aeo_panel("k", now=now, call=fake_call)

    assert len(results) == 20
    assert len({r.run_id for r in results}) == 1
    assert {r.date for r in results} == {"2026-09-14"}
    assert {r.week_num for r in results} == {38}
    errored = [r for r in results if r.error_message]
    assert len(errored) == 5 and all(r.engine == "gemini" and r.cited == "No" for r in errored)
    assert errored[0].content_snippet == "" and errored[0].competitors == []
    cited = [r for r in results if r.cited == "Yes"]
    assert {r.query_id for r in cited} == {"Q3"} and len(cited) == 3
    assert all(len(r.content_snippet) == 500 for r in results if not r.error_message)
    assert all(r.quote_type is None for r in results)


def test_run_panel_hard_ceiling_records_the_stalled_call() -> None:
    release = threading.Event()

    def stalled(model: str, _p: str, _k: str) -> aeo.CallResult:
        if model == "perplexity/sonar-pro":
            release.wait(5)
        return aeo.CallResult(content="", citations=[], raw={})

    try:
        results = aeo.run_aeo_panel("k", call=stalled, hard_ceiling_s=0.2)
    finally:
        release.set()
    stuck = [r for r in results if r.error_message]
    assert len(stuck) == 5
    assert stuck[0].error_message == "Hard ceiling 0.2s exceeded for perplexity/sonar-pro"


def test_summarize_run_excludes_errored_rows_from_the_cite_rate() -> None:
    base: dict[str, Any] = {
        "run_id": "r",
        "date": "d",
        "week_num": 1,
        "query_id": "Q1",
        "query_text": "q",
        "position": None,
        "cited_page": None,
        "quote_type": None,
        "competitors": [],
        "raw_citations": [],
        "content_snippet": "",
        "duration_ms": 1,
    }
    rows = [
        aeo.RunResult(engine="claude", engine_label="Claude", cited="Yes", **base),
        aeo.RunResult(engine="claude", engine_label="Claude", cited="Mentioned-no-link", **base),
        aeo.RunResult(
            engine="gemini", engine_label="Gemini", cited="No", error_message="x", **base
        ),
        aeo.RunResult(engine="gemini", engine_label="Gemini", cited="No", **base),
    ]
    s = aeo.summarize_run(rows)
    assert s == {
        "total": 4,
        "cited": 1,
        "mentioned": 1,
        "errored": 1,
        "cleanTotal": 3,
        "citeRate": 1 / 3,
        "byEngine": {
            "Claude": {"total": 2, "cited": 1, "errored": 0},
            "Gemini": {"total": 2, "cited": 0, "errored": 1},
        },
    }
    assert aeo.summarize_run([])["citeRate"] == 0


def test_citation_row_serialises_like_the_typescript() -> None:
    base: dict[str, Any] = {
        "run_id": "r",
        "date": "2026-09-14",
        "week_num": 38,
        "engine": "claude",
        "engine_label": "Claude",
        "query_id": "Q1",
        "query_text": "q",
        "cited": "Yes",
        "position": 1,
        "cited_page": OWN,
        "quote_type": None,
        "content_snippet": "s",
        "duration_ms": 5,
    }
    row = aeo.citation_row(
        aeo.RunResult(
            competitors=["a.example"], raw_citations=[aeo.CitationHit(OWN, 1, "T")], **base
        )
    )
    assert row.competitors == '["a.example"]'
    assert row.raw_citations == json.dumps(
        [{"url": OWN, "title": "T", "position": 1}], separators=(",", ":")
    )
    assert row.error_message is None


# ── execute_panel + route ─────────────────────────────────────────────────
def _fake_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_call(_m: str, _p: str, _k: str) -> aeo.CallResult:
        return aeo.CallResult(content="SaralPrivacy", citations=[aeo.CitationHit(OWN, 1)], raw={})

    monkeypatch.setattr(aeo, "call_openrouter", fake_call)


def test_execute_panel_persists_twenty_rows(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_panel(monkeypatch)
    ok, payload = aeo.execute_panel(session, "k")

    assert ok is True
    assert payload["ok"] is True
    assert payload["persisted"] == 20
    assert payload["dbErrors"] == []
    assert payload["summary"]["cited"] == 20
    rows = session.exec(select(AiCitation)).all()
    assert len(rows) == 20 and {r.cited for r in rows} == {"Yes"}


def test_execute_panel_reports_a_crash(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> list[aeo.RunResult]:
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(aeo, "run_aeo_panel", boom)
    ok, payload = aeo.execute_panel(session, "k")
    assert ok is False
    assert payload["ok"] is False and payload["error"] == "executor exploded"
    assert "durationMs" in payload


def test_route_needs_the_openrouter_key(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    res = client.post("/api/v1/admin/aeo-panel-run", headers=admin_headers)
    assert res.status_code == 500
    assert res.json()["detail"] == "OPENROUTER_API_KEY not configured in Vercel env vars"


def test_route_runs_in_the_background_and_the_task_carries_the_result(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk-test")
    _fake_panel(monkeypatch)

    res = client.post("/api/v1/admin/aeo-panel-run", headers=admin_headers)

    assert res.status_code == 202
    body = res.json()
    assert body["ok"] is True and body["status"] == "running"
    task = client.get(f"/api/v1/admin/tasks/{body['task_id']}", headers=admin_headers).json()
    assert task["kind"] == "aeo-panel"
    assert task["status"] == "done"
    assert task["result"]["ok"] is True
    assert task["result"]["persisted"] == 20
    assert task["finished_at"] is not None
    assert len(session.exec(select(AiCitation)).all()) == 20
    assert session.exec(select(AdminTask)).one().started_by is not None


def test_route_is_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.post("/api/v1/admin/aeo-panel-run", headers=blogger_headers).status_code == 403
    assert client.post("/api/v1/admin/aeo-panel-run").status_code == 401


def test_unknown_task_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.get(
        "/api/v1/admin/tasks/0190a1b2-0000-7000-8000-000000000000", headers=admin_headers
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Task not found"
