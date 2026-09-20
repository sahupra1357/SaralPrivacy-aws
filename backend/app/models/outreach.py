"""Outreach campaign tables: `ops.outreach_contacts` and `ops.email_send_log`.

Columns mirror `_backup/supabase/migrations/0001_initial_schema.sql` exactly, including the
renamed `created_at_attr` / `updated_at_attr` columns that `lib/db/supabase.ts` mapped
the application's `created_at` / `updated_at` fields onto.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

_TZ = DateTime(timezone=True)

# check (status in (...)) on ops.outreach_contacts
CONTACT_STATUSES = (
    "pending",
    "sent",
    "subscribed",
    "bounced",
    "unsubscribed",
    "complained",
    "failed",
)
# check (email_type in (...)) on ops.email_send_log
EMAIL_TYPES = (
    "intro",
    "briefing_daily",
    "briefing_weekly",
    "report",
    "subscribe_confirmation",
    "unsubscribe_confirmation",
)
# check (status in (...)) on ops.email_send_log
SEND_LOG_STATUSES = ("sent", "delivered", "opened", "clicked", "bounced", "complained")


def _ts(*, nullable: bool) -> Any:
    """A `timestamptz` column. Returns the FieldInfo, not a datetime."""
    if nullable:
        return Field(default=None, sa_type=_TZ, nullable=True)
    return Field(sa_type=_TZ, nullable=False)


class OutreachContact(TimestampMixin, SQLModel, table=True):
    __tablename__ = "outreach_contacts"
    __table_args__ = {"schema": "ops"}

    email: str = Field(unique=True)
    name: str | None = None
    company: str | None = None
    industry: str | None = None
    source: str | None = None
    status: str = Field(index=True)
    intro_sent_at: datetime | None = _ts(nullable=True)
    subscribed_at: datetime | None = _ts(nullable=True)
    magic_token: str = Field(unique=True)
    created_at_attr: datetime = _ts(nullable=False)


class EmailSendLog(TimestampMixin, SQLModel, table=True):
    __tablename__ = "email_send_log"
    __table_args__ = {"schema": "ops"}

    recipient_email: str = Field(index=True)
    email_type: str = Field(index=True)
    briefing_id: str | None = None
    resend_message_id: str | None = Field(default=None, index=True)
    status: str
    consent_basis: str
    sent_at: datetime = Field(sa_type=_TZ, nullable=False, index=True)
    updated_at_attr: datetime | None = _ts(nullable=True)
