"""Queries the chat routes need. No business logic, no HTTP concepts.

`ops.leads` and `ops.consent_log` belong to the forms module and `app.briefings_meta` to
editorial, so the two writes and the one read that touch them go through parameterised
statements against the column names in `_backup/supabase/migrations/0001_initial_schema.sql`
rather than importing another module's models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.models.chat import ChatFeedback

INSERT_LEAD = text(
    """
    insert into ops.leads (
        name, email, phone, company, industry, company_size, source, issue_summary,
        preferred_contact, preferred_time, consent_version, risk_level, created_at_attr,
        ip_address, city, country, region
    ) values (
        :name, :email, :phone, :company, :industry, :company_size, :source, :issue_summary,
        :preferred_contact, :preferred_time, :consent_version, :risk_level, :created_at,
        :ip_address, :city, :country, :region
    )
    returning id
    """
)

INSERT_CONSENT_LOG = text(
    """
    insert into ops.consent_log (
        email, name, source, consent_type, consent_value, privacy_version,
        ip_address, user_agent, city, country, region, timestamp
    ) values (
        :email, :name, :source, :consent_type, :consent_value, :privacy_version,
        :ip_address, :user_agent, :city, :country, :region, :timestamp
    )
    returning id
    """
)


def create_feedback(
    session: Session,
    *,
    session_id: str,
    turn_id: str,
    helpful: bool | None,
    reason: str | None,
    page_url: str | None,
    failure_kind: str | None,
    redacted_question: str | None,
    ts: datetime,
) -> ChatFeedback:
    row = ChatFeedback(
        session_id=session_id,
        turn_id=turn_id,
        helpful=helpful,
        reason=reason,
        page_url=page_url,
        failure_kind=failure_kind,
        redacted_question=redacted_question,
        ts=ts,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def insert_lead(session: Session, lead: dict[str, Any]) -> None:
    """Same row shape /api/contact writes, so handoff leads land in /admin/leads and
    /admin/consultations with no admin-side work. `source` is the only field that
    distinguishes them."""
    session.execute(INSERT_LEAD, lead)
    session.commit()


def insert_consent_log(session: Session, record: dict[str, Any]) -> None:
    session.execute(INSERT_CONSENT_LOG, record)
    session.commit()
