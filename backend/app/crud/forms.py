"""Queries for the public forms module. No HTTP concepts, no business rules.

Ports `frontend/lib/subscribers.ts` (`upsertSubscriber`) and
`frontend/lib/suppression.ts` (`isSuppressed`), plus the row writes the seven route
handlers performed through `lib/db`.
"""

import logging
from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session, select

from app.models.forms import (
    SUPPRESSED_STATUSES,
    ConsentLog,
    Download,
    Lead,
    Subscriber,
    SurveyResponse,
    TemplateDownload,
)

log = logging.getLogger(__name__)

PRIVACY_NOTICE_VERSION = "1.0.0"  # lib/utils.ts


def normalise_email(email: str) -> str:
    return email.strip().lower()


# ── generic inserts ───────────────────────────────────────────────────────
def create_lead(session: Session, lead: Lead) -> Lead:
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return lead


def create_download(session: Session, download: Download) -> Download:
    session.add(download)
    session.commit()
    session.refresh(download)
    return download


def create_survey_response(session: Session, response: SurveyResponse) -> SurveyResponse:
    session.add(response)
    session.commit()
    session.refresh(response)
    return response


def create_template_download(session: Session, row: TemplateDownload) -> TemplateDownload:
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def log_consent(
    session: Session,
    *,
    email: str,
    name: str | None,
    source: str,
    consent_type: str,
    timestamp: str,
    ip_address: str = "",
    user_agent: str = "",
    city: str = "",
    country: str = "",
    region: str = "",
    consent_value: bool = True,
    privacy_version: str = PRIVACY_NOTICE_VERSION,
) -> ConsentLog:
    """One row in the consent audit trail. Callers treat failures as non-fatal, exactly
    as the TypeScript did (`insertDocument(...).catch(console.error)`)."""
    row = ConsentLog(
        email=email,
        name=name,
        source=source,
        consent_type=consent_type,
        consent_value=consent_value,
        privacy_version=privacy_version,
        ip_address=ip_address,
        user_agent=user_agent,
        city=city,
        country=country,
        region=region,
        timestamp=timestamp,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── subscribers ───────────────────────────────────────────────────────────
def get_subscriber_by_email(session: Session, email: str) -> Subscriber | None:
    return session.exec(
        select(Subscriber).where(Subscriber.email == normalise_email(email))
    ).first()


def create_subscriber(session: Session, subscriber: Subscriber) -> Subscriber:
    session.add(subscriber)
    session.commit()
    session.refresh(subscriber)
    return subscriber


def reactivate_subscriber(session: Session, subscriber: Subscriber) -> Subscriber:
    """Re-subscribe after an unsubscribe: flip the existing row back rather than adding a
    second one (the old route inserted blindly and sent duplicate briefings)."""
    subscriber.status = "active"
    subscriber.consent_version = PRIVACY_NOTICE_VERSION
    session.add(subscriber)
    session.commit()
    session.refresh(subscriber)
    return subscriber


def unsubscribe(session: Session, subscriber: Subscriber, when: datetime) -> Subscriber:
    """Soft-unsubscribe: the row stays as a suppression record so a later re-import
    cannot re-contact them. Full deletion is the erasure right (/rights)."""
    subscriber.status = "unsubscribed"
    subscriber.unsubscribed_at = when
    session.add(subscriber)
    session.commit()
    session.refresh(subscriber)
    return subscriber


def upsert_subscriber(
    session: Session,
    *,
    email: str,
    source: str,
    name: str = "",
    industry: str = "",
    ip: str = "",
    user_agent: str = "",
    city: str = "",
    country: str = "",
    region: str = "",
    now: datetime,
) -> Subscriber | None:
    """lib/subscribers.ts `upsertSubscriber`: if a row already exists, do nothing at all
    (not even a consent-log entry). Returns the created row, or None when one existed.

    `source` names the acquisition surface and goes to `consent_log.source`, as before.
    `subscribers.consent_source` is NOT NULL with a five-value check constraint that
    `source` is not part of, so it is set to "manual" — the same value /subscribe uses.
    """
    normalised = normalise_email(email)
    if get_subscriber_by_email(session, normalised) is not None:
        return None

    subscriber = Subscriber(
        name=name,
        email=normalised,
        industry=industry,
        frequency="daily",
        status="active",
        consent_source="manual",
        consent_version=PRIVACY_NOTICE_VERSION,
        created_at_attr=now,
        ip_address=ip,
        city=city,
        country=country,
        region=region,
        user_agent=user_agent,
    )
    create_subscriber(session, subscriber)

    try:
        log_consent(
            session,
            email=normalised,
            name=name,
            source=source,
            consent_type="email_marketing",
            timestamp=now.isoformat(),
            ip_address=ip,
            user_agent=user_agent,
            city=city,
            country=country,
            region=region,
        )
    except Exception:  # noqa: BLE001 — audit row is best effort, as in the TypeScript
        log.exception("[upsert_subscriber] consent_log write failed")

    return subscriber


# ── suppression ───────────────────────────────────────────────────────────
_OUTREACH_STATUS = text(
    "select status from ops.outreach_contacts where lower(trim(email)) = :email limit 1"
)


def is_suppressed(session: Session, email: str) -> bool:
    """lib/suppression.ts: blocked when the subscriber *or* the outreach contact for this
    address is unsubscribed/bounced/complained.

    `ops.outreach_contacts` is modelled by the outreach module, so it is read with plain
    SQL here rather than importing across module boundaries.
    """
    normalised = normalise_email(email)

    subscriber = get_subscriber_by_email(session, normalised)
    if subscriber is not None and subscriber.status in SUPPRESSED_STATUSES:
        return True

    row = session.execute(_OUTREACH_STATUS, {"email": normalised}).first()
    return bool(row is not None and row[0] in SUPPRESSED_STATUSES)
