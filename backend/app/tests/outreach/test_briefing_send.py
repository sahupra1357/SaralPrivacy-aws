from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.jobs import outreach as jobs
from app.models.outreach import EmailSendLog
from app.services import email
from app.tests.conftest import Recorder
from app.tests.outreach.helpers import briefing_row, make_briefing, make_subscriber

PATH = "/api/v1/cron/briefing-send"
SECRET = "cron-test-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}
TUESDAY = datetime(2026, 9, 15, 4, 30, tzinfo=UTC)
MONDAY = datetime(2026, 9, 14, 4, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CRON_SECRET", SECRET)
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "link-secret")
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com")
    monkeypatch.setattr(jobs, "SEND_PAUSE_SECONDS", 0)
    monkeypatch.setattr(jobs, "now_utc", lambda: TUESDAY)


def test_briefing_send_mails_eligible_daily_subscribers(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    briefing_id = make_briefing(session, title="Consent notices go live")
    make_subscriber(session, "daily@example.com")
    make_subscriber(session, "DAILY@example.com")  # duplicate row, mailed once
    make_subscriber(session, "weekly@example.com", frequency="weekly")
    make_subscriber(session, "gone@example.com", status="unsubscribed")
    make_subscriber(session, "bounced@example.com", status="bounced")

    res = client.get(PATH, headers=AUTH)

    assert res.status_code == 200
    assert res.json() == {
        "sent": 1,
        "failed": 0,
        "total_eligible": 1,
        "briefing_used": "Consent notices go live",
        "briefing_id": briefing_id,
    }
    call = mock_email.last()
    assert call["to"] == ["daily@example.com"]
    assert call["subject"] == "DPDPA Daily Brief: Consent notices go live"
    assert call["from_"] == settings.EMAILS_FROM_BRIEFINGS
    unsub = jobs.build_unsubscribe_url("daily@example.com")
    assert call["headers"] == {"List-Unsubscribe": f"<{unsub}>"}
    assert "Friday, 18 September 2026" in call["html"]
    log = session.exec(select(EmailSendLog)).one()
    assert (log.email_type, log.consent_basis, log.status) == (
        "briefing_daily",
        "explicit_consent",
        "sent",
    )
    row = briefing_row(session, briefing_id)
    assert row["status"] == "sent"
    assert row["subscriber_count"] == 1
    assert row["sent_at"] is not None


def test_briefing_send_includes_weekly_subscribers_on_monday(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "now_utc", lambda: MONDAY)
    make_briefing(session)
    make_subscriber(session, "weekly@example.com", frequency="weekly")

    res = client.get(PATH, headers=AUTH)

    assert res.json()["sent"] == 1
    assert session.exec(select(EmailSendLog)).one().email_type == "briefing_weekly"


def test_briefing_send_counts_failures(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_briefing(session)
    make_subscriber(session, "a@example.com")

    def boom(*_a: object, **_kw: object) -> email.SentEmail:
        raise OSError("smtp down")

    monkeypatch.setattr(email, "send", boom)

    body = client.get(PATH, headers=AUTH).json()

    assert (body["sent"], body["failed"]) == (0, 1)


def test_briefing_send_reports_no_eligible_subscribers(
    client: TestClient, session: Session
) -> None:
    make_briefing(session, title="T")
    make_subscriber(session, "weekly@example.com", frequency="weekly")

    res = client.get(PATH, headers=AUTH)

    assert res.json() == {"message": "No eligible subscribers today.", "briefing_used": "T"}


def test_briefing_send_needs_an_approved_briefing(client: TestClient, session: Session) -> None:
    make_briefing(session, status="sent")

    res = client.get(PATH, headers=AUTH)

    assert res.status_code == 404
    assert res.json()["detail"] == "No approved briefing found. Approve a briefing first."


def test_briefing_send_requires_the_cron_secret(client: TestClient) -> None:
    assert client.get(PATH).status_code == 401


def test_run_briefing_send_job_twice_sends_once(session: Session, mock_email: Recorder) -> None:
    make_briefing(session)
    make_subscriber(session, "a@example.com")

    first = jobs.run_briefing_send(session)
    second = jobs.run_briefing_send(session)

    assert first.ok is True
    assert second.ok is False
    assert len(mock_email.calls) == 1


def test_send_briefing_to_subscribers_returns_sent_and_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def send(to: str, subject: str, _html: str, **_kw: object) -> email.SentEmail:
        if to == "bad@example.com":
            raise OSError("smtp down")
        return email.SentEmail(message_id="m", to=[to], subject=subject)

    monkeypatch.setattr(email, "send", send)
    subs = [
        jobs.EligibleSubscriber(id="1", email="ok@example.com", name="", frequency="daily"),
        jobs.EligibleSubscriber(id="2", email="bad@example.com", name="", frequency="daily"),
    ]

    assert jobs.send_briefing_to_subscribers({"title": "T"}, subs) == (1, 1)
