"""Queries for the assessments module.

`ops.consent_log` and `ops.subscribers` are shared with the forms module, so they are
written with plain SQL here rather than a second SQLModel declaration of the same
table (two wave-1 builders would collide in the shared metadata). The orchestrator may
collapse `upsert_subscriber` / `log_consent` onto the forms crud once both are merged.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.assessments import Assessment

PRIVACY_NOTICE_VERSION = "1.0.0"

_INSERT_CONSENT = text(
    """
    insert into ops.consent_log
        (email, name, source, consent_type, consent_value, privacy_version,
         ip_address, user_agent, city, country, region, timestamp)
    values
        (:email, :name, :source, :consent_type, :consent_value, :privacy_version,
         :ip_address, :user_agent, :city, :country, :region, :timestamp)
    """
)

_FIND_SUBSCRIBER = text("select 1 from ops.subscribers where lower(email) = :email limit 1")

_INSERT_SUBSCRIBER = text(
    """
    insert into ops.subscribers
        (name, email, industry, frequency, status, consent_version, consent_source,
         created_at_attr, ip_address, city, country, region, user_agent)
    values
        (:name, :email, :industry, 'daily', 'active', :consent_version, :consent_source,
         :created_at_attr, :ip_address, :city, :country, :region, :user_agent)
    """
)


def create_assessment(session: Session, values: dict[str, Any]) -> Assessment:
    row = Assessment(**values)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_by_report_token(session: Session, token: str) -> Assessment | None:
    return session.exec(select(Assessment).where(Assessment.report_token == token)).first()


def get_by_id(session: Session, assessment_id: UUID) -> Assessment | None:
    return session.get(Assessment, assessment_id)


def mark_report_email_sent(session: Session, assessment_id: UUID, *, when: datetime) -> None:
    """Records the auto-send exactly as `updateDocumentById(..., email_sent_by: "auto")` did."""
    row = session.get(Assessment, assessment_id)
    if row is None:
        return
    row.email_sent_at = when.isoformat()
    row.email_sent_by = "auto"
    session.add(row)
    session.commit()


def log_consent(
    session: Session,
    *,
    email: str,
    source: str,
    consent_type: str,
    name: str = "",
    ip_address: str = "",
    user_agent: str = "",
    city: str = "",
    country: str = "",
    region: str = "",
    timestamp: str | None = None,
) -> None:
    session.execute(
        _INSERT_CONSENT,
        {
            "email": email,
            "name": name,
            "source": source,
            "consent_type": consent_type,
            "consent_value": True,
            "privacy_version": PRIVACY_NOTICE_VERSION,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "city": city,
            "country": country,
            "region": region,
            "timestamp": timestamp or datetime.now(UTC).isoformat(),
        },
    )
    session.commit()


def upsert_subscriber(
    session: Session,
    *,
    email: str,
    source: str,
    name: str = "",
    industry: str = "",
    ip_address: str = "",
    user_agent: str = "",
    city: str = "",
    country: str = "",
    region: str = "",
) -> bool:
    """Port of `lib/subscribers.ts`: existing email wins, nothing is updated.

    Returns True when a row was created. The TypeScript passed the opt-in source as
    `source`, but `ops.subscribers` only has `consent_source` (CHECK allows
    'assessment_form'), so the value lands there — see the inventory's design notes.
    """
    normalised = email.strip().lower()
    if session.execute(_FIND_SUBSCRIBER, {"email": normalised}).first() is not None:
        return False

    now = datetime.now(UTC)
    session.execute(
        _INSERT_SUBSCRIBER,
        {
            "name": name,
            "email": normalised,
            "industry": industry,
            "consent_version": PRIVACY_NOTICE_VERSION,
            "consent_source": source,
            "created_at_attr": now,
            "ip_address": ip_address,
            "city": city,
            "country": country,
            "region": region,
            "user_agent": user_agent,
        },
    )
    session.commit()
    log_consent(
        session,
        email=normalised,
        name=name,
        source=source,
        consent_type="email_marketing",
        ip_address=ip_address,
        user_agent=user_agent,
        city=city,
        country=country,
        region=region,
        timestamp=now.isoformat(),
    )
    return True
