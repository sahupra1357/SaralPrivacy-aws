"""app/crud/assessments.py — one test per function."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.crud import assessments as crud


def _values(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "email": "crud@example.com",
        "industry": "retail",
        "risk_level": "Early Stage",
        "report_token": "token-crud-1",
        "report_token_expires_at": "2027-01-01T00:00:00+00:00",
        "created_at_attr": datetime.now(UTC),
    }
    base.update(over)
    return base


def test_create_assessment_persists_and_returns_the_row(session: Session) -> None:
    row = crud.create_assessment(session, _values())

    assert row.id is not None
    assert row.email == "crud@example.com"
    assert row.created_at is not None


def test_get_by_report_token_finds_the_row(session: Session) -> None:
    crud.create_assessment(session, _values())

    found = crud.get_by_report_token(session, "token-crud-1")

    assert found is not None
    assert found.industry == "retail"


def test_get_by_report_token_returns_none_when_missing(session: Session) -> None:
    assert crud.get_by_report_token(session, "no-such-token") is None


def test_get_by_id_round_trips(session: Session) -> None:
    row = crud.create_assessment(session, _values(report_token="token-crud-id"))

    assert crud.get_by_id(session, row.id) is not None


def test_mark_report_email_sent_records_auto_delivery(session: Session) -> None:
    row = crud.create_assessment(session, _values(report_token="token-crud-2"))
    when = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)

    crud.mark_report_email_sent(session, row.id, when=when)

    refreshed = crud.get_by_id(session, row.id)
    assert refreshed is not None
    assert refreshed.email_sent_by == "auto"
    assert refreshed.email_sent_at == when.isoformat()


def test_mark_report_email_sent_is_a_noop_for_an_unknown_id(session: Session) -> None:
    import uuid

    crud.mark_report_email_sent(session, uuid.uuid4(), when=datetime.now(UTC))  # must not raise


def test_log_consent_writes_the_privacy_version_of_the_day(session: Session) -> None:
    crud.log_consent(
        session,
        email="consent@example.com",
        source="assessment",
        consent_type="data_processing",
        ip_address="203.0.113.7",
    )

    row = session.execute(
        text(
            "select source, consent_type, consent_value, privacy_version, ip_address "
            "from ops.consent_log where email = :e"
        ),
        {"e": "consent@example.com"},
    ).one()
    assert row == ("assessment", "data_processing", True, "1.0.0", "203.0.113.7")


def test_upsert_subscriber_creates_an_active_daily_subscriber_and_a_consent_row(
    session: Session,
) -> None:
    created = crud.upsert_subscriber(
        session,
        email="  NEW@Example.com ",
        name="Nita",
        industry="retail",
        source="assessment_form",
    )

    assert created is True
    row = session.execute(
        text(
            "select email, name, industry, frequency, status, consent_version, consent_source "
            "from ops.subscribers where email = :e"
        ),
        {"e": "new@example.com"},
    ).one()
    assert row == (
        "new@example.com",
        "Nita",
        "retail",
        "daily",
        "active",
        "1.0.0",
        "assessment_form",
    )

    consent = session.execute(
        text("select consent_type from ops.consent_log where email = :e"),
        {"e": "new@example.com"},
    ).scalar_one()
    assert consent == "email_marketing"


def test_upsert_subscriber_leaves_an_existing_subscriber_untouched(session: Session) -> None:
    crud.upsert_subscriber(session, email="dup@example.com", name="First", source="assessment_form")

    created_again = crud.upsert_subscriber(
        session, email="DUP@example.com", name="Second", source="assessment_form"
    )

    assert created_again is False
    names = (
        session.execute(
            text("select name from ops.subscribers where email = :e"), {"e": "dup@example.com"}
        )
        .scalars()
        .all()
    )
    assert names == ["First"]
