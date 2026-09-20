"""Row builders shared by the outreach tests."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from app.models.forms import Subscriber
from app.models.outreach import OutreachContact


def make_contact(
    session: Session,
    email: str = "asha@example.com",
    *,
    status: str = "pending",
    name: str | None = "Asha Rao",
    token: str | None = None,
    created: datetime | None = None,
    industry: str | None = "Fintech",
) -> OutreachContact:
    row = OutreachContact(
        email=email,
        name=name,
        industry=industry,
        source="excel_import_v1",
        status=status,
        magic_token=token or f"tok-{uuid.uuid4().hex}",
        created_at_attr=created or datetime.now(UTC),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def make_subscriber(
    session: Session,
    email: str = "reader@example.com",
    *,
    status: str = "active",
    frequency: str | None = "daily",
    name: str = "Reader",
) -> Subscriber:
    row = Subscriber(
        name=name, email=email, status=status, frequency=frequency, consent_source="manual"
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


_INSERT_BRIEFING = text(
    "insert into app.briefings_meta "
    "(title, slug, summary, why_it_matters, action_checklist, status, scheduled_for, outreach_used_at) "
    "values (:title, :slug, :summary, :why, :checklist, :status, :scheduled_for, :used) "
    "returning id"
)


def make_briefing(
    session: Session,
    *,
    title: str = "Consent notices go live",
    status: str = "approved",
    scheduled_for: datetime | None = None,
    outreach_used_at: datetime | None = None,
    why: str | None = "Every notice must be itemised.",
    checklist: str | None = '["Review your notice", "Name a grievance officer"]',
    summary: str | None = "What changed this week.",
) -> str:
    params: dict[str, Any] = {
        "title": title,
        "slug": f"b-{uuid.uuid4().hex[:8]}",
        "summary": summary,
        "why": why,
        "checklist": checklist,
        "status": status,
        "scheduled_for": scheduled_for or datetime(2026, 9, 18, tzinfo=UTC),
        "used": outreach_used_at,
    }
    new_id = session.execute(_INSERT_BRIEFING, params).scalar_one()
    session.commit()
    return str(new_id)


def briefing_row(session: Session, briefing_id: str) -> dict[str, Any]:
    row = session.execute(
        text("select to_jsonb(b) from app.briefings_meta b where id = :id"),
        {"id": uuid.UUID(briefing_id)},
    ).scalar_one()
    return dict(row)
