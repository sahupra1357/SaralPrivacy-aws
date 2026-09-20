"""Queries for the outreach module. No HTTP concepts, no business rules.

`app.briefings_meta` is modelled by the editorial module; it is read and updated here
with parameterised SQL (`text()`) rather than by importing editorial's model. Each
briefing row is returned as a plain dict of its columns (`to_jsonb`), so a column the
editorial model adds later (for example `content`) is picked up without a change here.
"""

import logging
import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, text, update
from sqlmodel import Session, col, select

from app.models.forms import SUPPRESSED_STATUSES, Subscriber
from app.models.outreach import EmailSendLog, OutreachContact

log = logging.getLogger(__name__)

Briefing = dict[str, Any]


def normalise_email(email: str) -> str:
    return email.strip().lower()


# ── outreach_contacts ─────────────────────────────────────────────────────
def get_contact_by_token(session: Session, token: str) -> OutreachContact | None:
    return session.exec(select(OutreachContact).where(OutreachContact.magic_token == token)).first()


def existing_contact_emails(session: Session) -> set[str]:
    rows = session.exec(select(func.lower(OutreachContact.email))).all()
    return {str(r) for r in rows}


def insert_contact(session: Session, contact: OutreachContact) -> bool:
    """Insert one contact inside a SAVEPOINT. Returns False (and leaves the outer
    transaction usable) when the row is rejected, e.g. a unique-index race — the
    TypeScript counted only fulfilled inserts (`Promise.allSettled`)."""
    try:
        with session.begin_nested():
            session.add(contact)
            session.flush()
        return True
    except Exception:  # noqa: BLE001 — a rejected row is counted, not fatal
        log.warning("[outreach/import] contact insert rejected", exc_info=True)
        return False


def count_contacts_by_status(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(OutreachContact.status, func.count()).group_by(OutreachContact.status)
    ).all()
    return {str(status): int(n) for status, n in rows}


def list_contacts(
    session: Session, *, status: str | None, limit: int
) -> tuple[list[OutreachContact], int]:
    stmt = select(OutreachContact)
    count_stmt = select(func.count()).select_from(OutreachContact)
    if status:
        stmt = stmt.where(OutreachContact.status == status)
        count_stmt = count_stmt.where(OutreachContact.status == status)
    rows = session.exec(stmt.order_by(col(OutreachContact.created_at).desc()).limit(limit)).all()
    total = int(session.exec(count_stmt).one())
    return list(rows), total


def pending_contacts(session: Session, limit: int) -> list[OutreachContact]:
    return list(
        session.exec(
            select(OutreachContact)
            .where(OutreachContact.status == "pending")
            .order_by(col(OutreachContact.created_at_attr).asc())
            .limit(limit)
        ).all()
    )


def count_pending(session: Session) -> int:
    return int(
        session.exec(
            select(func.count())
            .select_from(OutreachContact)
            .where(OutreachContact.status == "pending")
        ).one()
    )


def save(session: Session, row: Any) -> None:
    session.add(row)
    session.commit()


def set_contact_status_by_email(session: Session, email: str, status: str) -> int:
    result = session.execute(
        update(OutreachContact)
        .where(func.lower(OutreachContact.email) == normalise_email(email))
        .values(status=status)
    )
    session.commit()
    return int(cast(CursorResult[Any], result).rowcount or 0)


# ── subscribers (forms module's table; shared audience) ──────────────────
def get_subscriber_by_email(session: Session, email: str) -> Subscriber | None:
    return session.exec(
        select(Subscriber).where(func.lower(Subscriber.email) == normalise_email(email))
    ).first()


def set_subscriber_status_by_email(session: Session, email: str, status: str) -> int:
    result = session.execute(
        update(Subscriber)
        .where(func.lower(Subscriber.email) == normalise_email(email))
        .values(status=status)
    )
    session.commit()
    return int(cast(CursorResult[Any], result).rowcount or 0)


def eligible_subscribers(session: Session) -> list[Subscriber]:
    return list(
        session.exec(
            select(Subscriber)
            .where(col(Subscriber.status).not_in(SUPPRESSED_STATUSES))
            .order_by(col(Subscriber.created_at).asc())
        ).all()
    )


# ── email_send_log ────────────────────────────────────────────────────────
def log_send(
    session: Session,
    *,
    recipient_email: str,
    email_type: str,
    message_id: str | None,
    status: str,
    consent_basis: str,
    sent_at: datetime,
    briefing_id: str | None = None,
) -> EmailSendLog:
    row = EmailSendLog(
        recipient_email=recipient_email,
        email_type=email_type,
        resend_message_id=message_id,
        status=status,
        consent_basis=consent_basis,
        sent_at=sent_at,
        briefing_id=briefing_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update_send_log_status(session: Session, message_id: str, status: str, when: datetime) -> int:
    """Webhook event → the send-log rows that carry this provider message id."""
    result = session.execute(
        update(EmailSendLog)
        .where(col(EmailSendLog.resend_message_id) == message_id)
        .values(status=status, updated_at_attr=when)
    )
    session.commit()
    return int(cast(CursorResult[Any], result).rowcount or 0)


# ── app.briefings_meta (editorial's table, read with plain SQL) ───────────
_BRIEFING_UNUSED = text(
    "select to_jsonb(b) from app.briefings_meta b "
    "where b.status in ('approved', 'sent') and b.outreach_used_at is null "
    "order by b.scheduled_for asc nulls last limit 1"
)
_BRIEFING_LATEST_USED = text(
    "select to_jsonb(b) from app.briefings_meta b "
    "where b.status in ('approved', 'sent') "
    "order by b.scheduled_for desc nulls last limit 1"
)
_BRIEFING_LATEST_APPROVED = text(
    "select to_jsonb(b) from app.briefings_meta b "
    "where b.status = 'approved' "
    "order by b.scheduled_for desc nulls last limit 1"
)
_MARK_OUTREACH_USED = text(
    "update app.briefings_meta set outreach_used_at = :now, updated_at = now() where id = :id"
)
_MARK_SENT = text(
    "update app.briefings_meta "
    "set status = 'sent', sent_at = :now, subscriber_count = :count, updated_at = now() "
    "where id = :id"
)


def _one_briefing(session: Session, stmt: Any) -> Briefing | None:
    row = session.execute(stmt).first()
    return dict(row[0]) if row is not None else None


def briefing_for_outreach(session: Session) -> tuple[Briefing, bool] | None:
    """Oldest approved/sent briefing not yet used in outreach (is_new=True); else the most
    recent approved/sent one (is_new=False); else None."""
    unused = _one_briefing(session, _BRIEFING_UNUSED)
    if unused is not None:
        return unused, True
    fallback = _one_briefing(session, _BRIEFING_LATEST_USED)
    if fallback is not None:
        return fallback, False
    return None


def latest_approved_briefing(session: Session) -> Briefing | None:
    return _one_briefing(session, _BRIEFING_LATEST_APPROVED)


def mark_briefing_outreach_used(session: Session, briefing_id: str, when: datetime) -> None:
    session.execute(_MARK_OUTREACH_USED, {"now": when, "id": uuid.UUID(str(briefing_id))})
    session.commit()


def mark_briefing_sent(session: Session, briefing_id: str, when: datetime, count: int) -> None:
    session.execute(_MARK_SENT, {"now": when, "count": count, "id": uuid.UUID(str(briefing_id))})
    session.commit()
