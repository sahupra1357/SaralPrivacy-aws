"""Unit tests for app/crud/forms.py — the port of lib/subscribers.ts and
lib/suppression.ts, plus the row writers the routes share."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.crud import forms as crud
from app.models.forms import ConsentLog, Subscriber

NOW = datetime(2026, 9, 18, 6, 30, tzinfo=UTC)


def test_normalise_email_trims_and_lowercases() -> None:
    assert crud.normalise_email("  Foo@Example.COM ") == "foo@example.com"


def test_upsert_subscriber_creates_the_row_and_a_consent_entry(session: Session) -> None:
    created = crud.upsert_subscriber(
        session,
        email="  New@Example.com ",
        name="New Person",
        industry="Retail",
        source="whitepaper_form",
        ip="1.2.3.4",
        user_agent="agent/1",
        city="Pune",
        country="IN",
        region="MH",
        now=NOW,
    )

    assert created is not None
    assert created.email == "new@example.com"
    assert created.status == "active"
    assert created.frequency == "daily"
    assert created.consent_source == "manual"
    assert created.consent_version == "1.0.0"
    assert created.created_at_attr == NOW

    consent = session.exec(select(ConsentLog)).one()
    assert consent.email == "new@example.com"
    assert consent.source == "whitepaper_form"
    assert consent.consent_type == "email_marketing"
    assert consent.user_agent == "agent/1"


def test_upsert_subscriber_does_nothing_when_the_email_already_exists(
    session: Session,
) -> None:
    session.add(
        Subscriber(
            name="Old", email="dup@example.com", status="unsubscribed", consent_source="manual"
        )
    )
    session.commit()

    assert crud.upsert_subscriber(session, email="DUP@example.com", source="x", now=NOW) is None

    rows = session.exec(select(Subscriber)).all()
    assert len(rows) == 1
    assert rows[0].status == "unsubscribed"  # untouched, as the TypeScript left it
    assert session.exec(select(ConsentLog)).first() is None


def test_unsubscribe_stamps_the_withdrawal(session: Session) -> None:
    row = Subscriber(name="A", email="a@example.com", status="active", consent_source="manual")
    session.add(row)
    session.commit()

    crud.unsubscribe(session, row, NOW)

    assert row.status == "unsubscribed"
    assert row.unsubscribed_at == NOW


def test_reactivate_subscriber_restores_active_and_the_consent_version(session: Session) -> None:
    row = Subscriber(
        name="A",
        email="a@example.com",
        status="bounced",
        consent_source="manual",
        consent_version="0.1.0",
    )
    session.add(row)
    session.commit()

    crud.reactivate_subscriber(session, row)

    assert row.status == "active"
    assert row.consent_version == "1.0.0"


@pytest.mark.parametrize("status", ["unsubscribed", "bounced", "complained"])
def test_is_suppressed_blocks_every_suppressed_subscriber_status(
    session: Session, status: str
) -> None:
    session.add(
        Subscriber(name="A", email="blocked@example.com", status=status, consent_source="manual")
    )
    session.commit()

    assert crud.is_suppressed(session, " Blocked@Example.com ") is True


def test_is_suppressed_allows_an_active_subscriber(session: Session) -> None:
    session.add(
        Subscriber(name="A", email="ok@example.com", status="active", consent_source="manual")
    )
    session.commit()

    assert crud.is_suppressed(session, "ok@example.com") is False


def test_is_suppressed_allows_an_unknown_address(session: Session) -> None:
    assert crud.is_suppressed(session, "stranger@example.com") is False


def test_is_suppressed_also_reads_outreach_contacts(session: Session) -> None:
    session.execute(
        text(
            "insert into ops.outreach_contacts "
            "(email, name, status, magic_token, created_at_attr) "
            "values ('lead@example.com', 'Lead', 'complained', 'tok-1', now())"
        )
    )
    session.commit()

    assert crud.is_suppressed(session, "lead@example.com") is True


def test_log_consent_defaults_to_the_current_privacy_notice_version(session: Session) -> None:
    row = crud.log_consent(
        session,
        email="c@example.com",
        name="C",
        source="contact",
        consent_type="data_processing",
        timestamp=NOW.isoformat(),
    )

    assert row.privacy_version == "1.0.0"
    assert row.consent_value is True
    assert row.timestamp == NOW.isoformat()
