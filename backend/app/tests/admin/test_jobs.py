"""app/jobs/admin.py — the weekly AEO panel and SEO inspection jobs."""

from typing import Any

import pytest
from sqlmodel import Session, select

from app.api.routes import aeo, seo
from app.core.config import settings
from app.jobs import admin as jobs
from app.models.admin import AiCitation

SA_JSON = '{"type":"service_account","client_email":"a@b.iam","private_key":"k"}'


def test_crons_match_the_old_schedules() -> None:
    assert jobs.AEO_PANEL_CRON == "30 3 * * 1"  # vercel.json
    assert jobs.SEO_INSPECT_CRON == "0 4 * * 1"  # .github/workflows/seo-inspect.yml


def test_aeo_job_fails_cleanly_without_the_key(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
    result = jobs.run_aeo_panel(session)
    assert result.ok is False
    assert result.summary == "OPENROUTER_API_KEY not configured"
    assert session.exec(select(AiCitation)).all() == []


def test_aeo_job_persists_one_run_per_firing(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk")
    monkeypatch.setattr(
        aeo,
        "call_openrouter",
        lambda _m, _p, _k: aeo.CallResult(content="", citations=[], raw={}),
    )

    first = jobs.run_aeo_panel(session)
    second = jobs.run_aeo_panel(session)

    assert first.ok and second.ok
    assert first.summary == "0/20 clean prompts cited · 0 errored · persisted 20"
    rows = session.exec(select(AiCitation)).all()
    # Each firing is its own run (as each Vercel cron firing was): 20 rows per run_id.
    assert len(rows) == 40
    assert len({r.run_id for r in rows}) == 2


def test_aeo_job_reports_a_crash(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "sk")
    monkeypatch.setattr(
        aeo, "execute_panel", lambda _s, _k: (False, {"ok": False, "error": "boom"})
    )
    result = jobs.run_aeo_panel(session)
    assert result.ok is False and result.summary == "boom"


def test_seo_job_needs_the_key(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", "")
    assert jobs.run_seo_inspect(session).ok is False
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", "{")
    bad = jobs.run_seo_inspect(session)
    assert bad.ok is False and bad.summary == "GSC_SERVICE_ACCOUNT_JSON: not valid JSON"


def test_seo_job_runs_full_scope_without_resubmitting(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", SA_JSON)
    captured: dict[str, Any] = {}

    def fake(_s: Session, **kw: Any) -> tuple[bool, dict[str, Any]]:
        captured.update(kw)
        return True, {"ok": True, "verdict": "TOO_EARLY", "summary": "3 weeks…", "log": ["a"]}

    monkeypatch.setattr(seo, "execute_inspection", fake)
    result = jobs.run_seo_inspect(session)

    assert result.ok is True
    assert result.summary == "TOO_EARLY — 3 weeks…"
    assert captured["scope"] == "full" and captured["submit_sitemap"] is False


def test_seo_job_surfaces_a_failed_run(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", SA_JSON)
    monkeypatch.setattr(
        seo,
        "execute_inspection",
        lambda _s, **_k: (False, {"ok": False, "error": "quota", "log": []}),
    )
    result = jobs.run_seo_inspect(session)
    assert result.ok is False and result.summary == "quota"
