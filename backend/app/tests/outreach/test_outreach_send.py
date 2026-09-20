from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.jobs import outreach as jobs
from app.models.outreach import EmailSendLog, OutreachContact
from app.services import email
from app.tests.conftest import Recorder
from app.tests.outreach.helpers import briefing_row, make_briefing, make_contact

PATH = "/api/v1/cron/outreach-send"
SECRET = "cron-test-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture(autouse=True)
def _cron(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CRON_SECRET", SECRET)
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com/")
    monkeypatch.setattr(settings, "OUTREACH_DAILY_CAP", 2)


def test_outreach_send_mails_pending_contacts_and_logs_each_send(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    briefing_id = make_briefing(session, title="Consent notices go live")
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    first = make_contact(session, "a@example.com", token="tok-a", created=t0)
    second = make_contact(session, "b@example.com", token="tok-b", created=t0 + timedelta(hours=1))
    make_contact(session, "c@example.com", token="tok-c", created=t0 + timedelta(hours=2))

    res = client.get(PATH, headers=AUTH)

    assert res.status_code == 200
    assert res.json() == {
        "sent": 2,
        "failed": 0,
        "remaining": 1,
        "briefing_used": "Consent notices go live",
        "briefing_id": briefing_id,
    }
    call = mock_email.calls[0]
    assert call["to"] == ["a@example.com"]
    assert call["subject"] == "Consent notices go live"
    assert call["from_"] == "SaralPrivacy <briefings@news.saralprivacy.com>"
    assert call["reply_to"] == "privacy@saralprivacy.com"
    assert "https://saralprivacy.com/subscribe?token=tok-a" in call["html"]
    assert "https://saralprivacy.com/unsubscribe/outreach?token=tok-a" in call["text"]
    assert "Hi Asha," in call["text"]
    for c in (first, second):
        session.refresh(c)
        assert c.status == "sent"
        assert c.intro_sent_at is not None
    logs = session.exec(select(EmailSendLog).order_by(EmailSendLog.recipient_email)).all()
    assert [(r.recipient_email, r.email_type, r.status, r.consent_basis) for r in logs] == [
        ("a@example.com", "intro", "sent", "one_time_dpdpa_sensitization"),
        ("b@example.com", "intro", "sent", "one_time_dpdpa_sensitization"),
    ]
    assert logs[0].resend_message_id == "test-1"
    assert briefing_row(session, briefing_id)["outreach_used_at"] is not None


def test_outreach_send_marks_failed_contacts_and_continues(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_briefing(session)
    bad = make_contact(session, "bad@example.com", created=datetime(2026, 9, 1, tzinfo=UTC))
    good = make_contact(session, "good@example.com", created=datetime(2026, 9, 2, tzinfo=UTC))

    def flaky_send(to: str, subject: str, _html: str, **_kw: object) -> email.SentEmail:
        if to == "bad@example.com":
            raise OSError("smtp down")
        return email.SentEmail(message_id="ok-1", to=[to], subject=subject)

    monkeypatch.setattr(email, "send", flaky_send)

    res = client.get(PATH, headers=AUTH)

    assert res.json()["sent"] == 1
    assert res.json()["failed"] == 1
    session.refresh(bad)
    session.refresh(good)
    assert bad.status == "failed"
    assert good.status == "sent"


def test_outreach_send_prefers_the_oldest_unused_briefing(
    client: TestClient, session: Session
) -> None:
    make_briefing(session, title="Used", outreach_used_at=datetime(2026, 9, 1, tzinfo=UTC))
    make_briefing(session, title="Newer", scheduled_for=datetime(2026, 9, 20, tzinfo=UTC))
    make_briefing(
        session, title="Older", status="sent", scheduled_for=datetime(2026, 9, 10, tzinfo=UTC)
    )
    make_briefing(
        session, title="Draft", status="draft", scheduled_for=datetime(2026, 9, 1, tzinfo=UTC)
    )
    make_contact(session)

    assert client.get(PATH, headers=AUTH).json()["briefing_used"] == "Older"


def test_outreach_send_reuses_the_latest_briefing_without_marking_it(
    client: TestClient, session: Session
) -> None:
    used_at = datetime(2026, 9, 1, tzinfo=UTC)
    briefing_id = make_briefing(session, title="Only", outreach_used_at=used_at)
    make_contact(session)

    res = client.get(PATH, headers=AUTH)

    assert res.json()["briefing_used"] == "Only"
    assert briefing_row(session, briefing_id)["outreach_used_at"].startswith("2026-09-01")


def test_outreach_send_reports_campaign_complete(client: TestClient, session: Session) -> None:
    make_briefing(session, title="T")

    res = client.get(PATH, headers=AUTH)

    assert res.status_code == 200
    assert res.json() == {
        "message": "No pending contacts. Campaign complete.",
        "briefing_used": "T",
    }


def test_outreach_send_needs_an_approved_briefing(client: TestClient, session: Session) -> None:
    make_contact(session)

    res = client.get(PATH, headers=AUTH)

    assert res.status_code == 404
    assert res.json()["detail"] == "No approved briefing found. Approve a briefing first."


def test_outreach_send_requires_the_cron_secret(client: TestClient) -> None:
    res = client.get(PATH, headers={"Authorization": "Bearer wrong"})

    assert res.status_code == 401
    assert res.json()["detail"] == "Unauthorized"


def test_outreach_send_is_locked_when_cron_secret_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CRON_SECRET", "")

    assert client.get(PATH, headers={"Authorization": "Bearer "}).status_code == 401


def test_run_outreach_send_job_twice_never_mails_a_contact_twice(
    session: Session, mock_email: Recorder
) -> None:
    make_briefing(session)
    make_contact(session, "once@example.com")

    first = jobs.run_outreach_send(session)
    second = jobs.run_outreach_send(session)

    assert first.ok and first.details and first.details["sent"] == 1
    assert second.ok and second.summary == "No pending contacts. Campaign complete."
    assert [c["to"] for c in mock_email.calls] == [["once@example.com"]]
    assert session.exec(select(OutreachContact)).one().status == "sent"


def test_run_outreach_send_job_reports_missing_briefing(session: Session) -> None:
    result = jobs.run_outreach_send(session)

    assert result.ok is False
    assert result.summary == "No approved briefing found. Approve a briefing first."
