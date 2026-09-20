from datetime import UTC, datetime

from sqlmodel import Session

from app.crud import outreach as crud
from app.models.outreach import OutreachContact
from app.tests.outreach.helpers import make_briefing, make_contact, make_subscriber


def test_get_contact_by_token(session: Session) -> None:
    contact = make_contact(session, token="find-me")

    assert crud.get_contact_by_token(session, "find-me") == contact
    assert crud.get_contact_by_token(session, "missing") is None


def test_existing_contact_emails_are_lowercased(session: Session) -> None:
    make_contact(session, "Mixed@Example.com")

    assert "mixed@example.com" in crud.existing_contact_emails(session)


def test_insert_contact_reports_a_rejected_duplicate(session: Session) -> None:
    make_contact(session, "dup@example.com")
    clash = OutreachContact(
        email="dup@example.com",
        status="pending",
        magic_token="t-x",
        created_at_attr=datetime.now(UTC),
    )

    assert crud.insert_contact(session, clash) is False
    assert crud.count_pending(session) == 1


def test_count_contacts_by_status(session: Session) -> None:
    make_contact(session, "a@example.com")
    make_contact(session, "b@example.com", status="sent")

    assert crud.count_contacts_by_status(session) == {"pending": 1, "sent": 1}


def test_pending_contacts_oldest_first_and_capped(session: Session) -> None:
    make_contact(session, "late@example.com", created=datetime(2026, 9, 2, tzinfo=UTC))
    make_contact(session, "early@example.com", created=datetime(2026, 9, 1, tzinfo=UTC))
    make_contact(session, "done@example.com", status="sent")

    assert [c.email for c in crud.pending_contacts(session, 1)] == ["early@example.com"]


def test_list_contacts_filters_and_totals(session: Session) -> None:
    make_contact(session, "a@example.com")
    make_contact(session, "b@example.com", status="sent")

    rows, total = crud.list_contacts(session, status="sent", limit=10)

    assert [r.email for r in rows] == ["b@example.com"]
    assert total == 1


def test_status_updates_by_email_are_case_insensitive(session: Session) -> None:
    make_contact(session, "Case@Example.com")
    make_subscriber(session, "Case@Example.com")

    assert crud.set_contact_status_by_email(session, "case@example.com", "bounced") == 1
    assert crud.set_subscriber_status_by_email(session, "CASE@example.com", "bounced") == 1


def test_eligible_subscribers_excludes_suppressed(session: Session) -> None:
    make_subscriber(session, "ok@example.com")
    make_subscriber(session, "no@example.com", status="complained")

    assert [s.email for s in crud.eligible_subscribers(session)] == ["ok@example.com"]


def test_send_log_status_update_by_message_id(session: Session) -> None:
    now = datetime.now(UTC)
    crud.log_send(
        session,
        recipient_email="a@example.com",
        email_type="intro",
        message_id="m1",
        status="sent",
        consent_basis="x",
        sent_at=now,
    )

    assert crud.update_send_log_status(session, "m1", "opened", now) == 1
    assert crud.update_send_log_status(session, "m2", "opened", now) == 0


def test_briefing_queries_and_marks(session: Session) -> None:
    approved = make_briefing(session, title="A", scheduled_for=datetime(2026, 9, 10, tzinfo=UTC))
    make_briefing(session, title="S", status="sent", scheduled_for=datetime(2026, 9, 5, tzinfo=UTC))

    latest = crud.latest_approved_briefing(session)
    assert latest is not None and latest["title"] == "A"

    found = crud.briefing_for_outreach(session)
    assert found is not None and found[0]["title"] == "S" and found[1] is True

    now = datetime.now(UTC)
    crud.mark_briefing_sent(session, approved, now, 7)
    assert crud.latest_approved_briefing(session) is None
