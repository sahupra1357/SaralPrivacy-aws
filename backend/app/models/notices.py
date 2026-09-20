"""Notice Pack Builder tables.

Columns match `_backup/supabase/migrations/0001_initial_schema.sql` exactly, including the
Appwrite-era `created_at_attr` rename that `lib/db/supabase.ts` mapped for these two
collections (the app-side `created_at` string landed in `created_at_attr`; the real
`created_at` timestamptz is the row's own insert time).

`app.notice_runs`, `app.business_profiles`, `app.dsar_requests` and `ops.chat_feedback`
also belong to this module per BUILD_PLAN §3 but no notices route reads or writes them;
they are listed for the orchestrator in `docs/build/status/notices.md`.
"""

from sqlalchemy import BigInteger, Index, Text
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class NoticeCapture(TimestampMixin, SQLModel, table=True):
    __tablename__ = "notice_captures"
    __table_args__ = (
        Index("ix_notice_captures_idx_created", "created_at"),
        Index("ix_notice_captures_idx_sector", "sector"),
        Index("ix_notice_captures_idx_source", "source"),
        {"schema": "app"},
    )

    email: str = Field(sa_type=Text)
    name: str | None = Field(default=None, sa_type=Text)
    business_name: str | None = Field(default=None, sa_type=Text)
    sector: str | None = Field(default=None, sa_type=Text)
    readiness_score: int | None = Field(default=None, sa_type=BigInteger)
    export_type: str | None = Field(default=None, sa_type=Text)
    source: str | None = Field(default=None, sa_type=Text)
    consent: bool | None = None
    ip_address: str | None = Field(default=None, sa_type=Text)
    city: str | None = Field(default=None, sa_type=Text)
    country: str | None = Field(default=None, sa_type=Text)
    created_at_attr: str | None = Field(default=None, sa_type=Text)


class NoticeEvent(TimestampMixin, SQLModel, table=True):
    __tablename__ = "notice_events"
    __table_args__ = (
        Index("ix_notice_events_idx_created", "created_at"),
        Index("ix_notice_events_idx_name", "name"),
        {"schema": "app"},
    )

    name: str = Field(sa_type=Text)
    session_id: str | None = Field(default=None, sa_type=Text)
    payload: str | None = Field(default=None, sa_type=Text)
    created_at_attr: str | None = Field(default=None, sa_type=Text)
