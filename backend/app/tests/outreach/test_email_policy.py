"""sendGateway + template helpers in app.jobs.outreach."""

import hashlib
import hmac
from datetime import UTC, datetime

import pytest
from sqlmodel import Session

from app.core.config import settings
from app.jobs import outreach as jobs
from app.tests.outreach.helpers import make_subscriber


def test_build_unsubscribe_url_signs_the_normalised_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "k")
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com/")
    sig = hmac.new(b"k", b"a+b@example.com", hashlib.sha256).hexdigest()

    assert jobs.build_unsubscribe_url("a+b@example.com") == (
        f"https://saralprivacy.com/unsubscribe?email=a%2Bb%40example.com&sig={sig}"
    )


def test_build_unsubscribe_url_is_unsigned_without_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "")
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com")

    assert (
        jobs.build_unsubscribe_url("a@example.com")
        == "https://saralprivacy.com/unsubscribe?email=a%40example.com"
    )


def test_eligible_subscribers_dedupes_and_defaults_frequency(session: Session) -> None:
    make_subscriber(session, "Same@Example.com", frequency=None)
    make_subscriber(session, "same@example.com", frequency="weekly")
    make_subscriber(session, "gone@example.com", status="unsubscribed")

    subs = jobs.eligible_subscribers(session)

    assert [(s.email, s.frequency) for s in subs] == [("same@example.com", "daily")]


def test_format_long_date_matches_en_in() -> None:
    assert (
        jobs.format_long_date(datetime(2026, 9, 18, 4, tzinfo=UTC)) == "Friday, 18 September 2026"
    )


def test_parse_checklist_tolerates_bad_json() -> None:
    assert jobs.parse_checklist('["a", "b"]') == ["a", "b"]
    assert jobs.parse_checklist("not json") == []
    assert jobs.parse_checklist('{"a": 1}') == []
    assert jobs.parse_checklist(None) == []


def test_why_text_prefers_hook_line_then_why_then_raw() -> None:
    assert jobs.why_text('{"hook_line1": "Hook", "why": "Why"}') == "Hook"
    assert jobs.why_text('{"why": "Why"}') == "Why"
    assert jobs.why_text("Plain sentence.") == "Plain sentence."
    assert jobs.why_text(None) == ""


def test_render_outreach_email_uses_first_name_or_there() -> None:
    briefing = {
        "title": "T",
        "summary": "S",
        "why_it_matters": "W",
        "action_checklist": '["Do it"]',
    }

    subject, html, text = jobs.render_outreach_email(
        briefing, name="Asha Rao", subscribe_url="https://x/s", unsubscribe_url="https://x/u"
    )
    _, _, anon = jobs.render_outreach_email(
        briefing, name=None, subscribe_url="https://x/s", unsubscribe_url="https://x/u"
    )

    assert subject == "T"
    assert "Hi Asha, here is today's DPDPA briefing" in html
    assert "Subscribe to Daily Briefings →" in html
    assert "YOUR ACTION CHECKLIST\n1. Do it" in text
    assert "Remove me: https://x/u" in text
    assert anon.startswith("Hi there,")


def test_render_briefing_email_subject_and_sections() -> None:
    subject, html = jobs.render_briefing_email(
        {
            "title": "T",
            "why_it_matters": "Because",
            "action_checklist": '["Step"]',
            "scheduled_for": "2026-09-18T00:00:00+00:00",
        },
        "https://x/unsub",
    )

    assert subject == "DPDPA Daily Brief: T"
    assert "Why It Matters" in html
    assert "Your Action Checklist" in html
    assert "Step" in html
    assert 'href="https://x/unsub"' in html
    assert "Friday, 18 September 2026" in html
