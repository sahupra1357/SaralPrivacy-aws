"""Editorial test fixtures: auth headers (re-exported), row factories, secrets, and
recorders for the outreach broadcast the approve/send routes delegate to."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.models.editorial import BlogPost, Briefing
from app.tests.auth.fixtures import (  # noqa: F401  (pytest collects these by name)
    admin_headers,
    admin_user,
    blogger_headers,
    blogger_user,
)

CRON = "test-cron-secret"


@pytest.fixture(autouse=True)
def cron_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "CRON_SECRET", CRON)
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.test")
    monkeypatch.setattr(settings, "GITHUB_TOKEN", "")  # never push briefings to GitHub from tests
    return CRON


@pytest.fixture
def cron_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {CRON}"}


@pytest.fixture
def make_briefing(session: Session) -> Callable[..., Briefing]:
    def _make(**overrides: Any) -> Briefing:
        data: dict[str, Any] = {
            "title": "Can You Keep Ex-Employee Records Forever?",
            "slug": "2026-09-18-can-you-keep-ex-employee-records-forever",
            "summary": "Old records are a liability.",
            "why_it_matters": "",
            "action_checklist": '["Delete old CVs"]',
            "status": "approved",
            "approval_token": "tok-123",
            "category": "learn",
            "tags": '["explainer"]',
            "industries": '["general"]',
            "author": "DPDPA Editorial Team",
            "read_time": 3,
            "featured": False,
            "created_at_attr": datetime(2026, 9, 18, 3, 30, tzinfo=UTC),
        }
        data.update(overrides)
        row = Briefing(**data)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    return _make


@pytest.fixture
def make_post(session: Session) -> Callable[..., BlogPost]:
    def _make(**overrides: Any) -> BlogPost:
        data: dict[str, Any] = {
            "title": "What the DPDP Rules change",
            "slug": "what-the-dpdp-rules-change",
            "excerpt": "A plain-language guide.",
            "lane": "law-explained",
            "author": "Desk",
            "status": "published",
            "section_what_changed": "Rules notified.",
            "section_law_says": "Section 6 consent.",
            "sections_json": "{}",
        }
        data.update(overrides)
        row = BlogPost(**data)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    return _make


@dataclass
class FakeSubscriber:
    id: str
    email: str
    name: str = ""
    frequency: str = "daily"


@dataclass
class BroadcastRecorder:
    subscribers: list[FakeSubscriber]
    calls: list[dict[str, Any]]
    failed: int = 0


@pytest.fixture
def broadcast(monkeypatch: pytest.MonkeyPatch) -> BroadcastRecorder:
    """Patch outreach's audience + sender (the approve/send routes delegate to them)."""
    from app.jobs import outreach

    rec = BroadcastRecorder(
        subscribers=[FakeSubscriber("s1", "a@example.com"), FakeSubscriber("s2", "b@example.com")],
        calls=[],
    )
    monkeypatch.setattr(outreach, "eligible_subscribers", lambda session: list(rec.subscribers))

    def fake_send(briefing: dict[str, Any], subscribers: list[Any]) -> tuple[int, int]:
        rec.calls.append({"briefing": briefing, "subscribers": subscribers})
        return len(subscribers) - rec.failed, rec.failed

    monkeypatch.setattr(outreach, "send_briefing_to_subscribers", fake_send)
    return rec
