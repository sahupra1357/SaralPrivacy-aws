"""Tables owned by the admin module.

- `ops.blogger_accounts` and `ops.ai_citations`: columns exactly as
  `_backup/supabase/migrations/0001_initial_schema.sql` (TimestampMixin supplies id / legacy_id /
  created_at / updated_at). `crud.auth` reads and updates `blogger_accounts.active`,
  `invite_token` and `updated_at` with raw SQL, so those columns must stay.
- `ops.seo_runs`, `ops.seo_inspections`, `ops.seo_index_requests`: exactly as
  `_backup/supabase/migrations/0005_ops_seo_inspections.sql`. They have no `legacy_id` /
  `updated_at` (and the ledger has no `id` at all), so they do NOT inherit TimestampMixin.
- `ops.admin_tasks`: new. Status of the admin-triggered long runs (AEO panel, SEO
  inspection) that now run as background tasks and are polled by the "Run now" buttons.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, utcnow

SEO_SCOPES = ("watchlist", "newcomers", "full")
SEO_VERDICT_CODES = ("QUEUE_MOVED", "STARVED", "TOO_EARLY", "INSUFFICIENT_DATA")
SEO_BUCKETS = (
    "indexed",
    "discovered",
    "crawled_not_indexed",
    "unknown",
    "excluded",
    "other",
    "error",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


class BloggerAccount(TimestampMixin, SQLModel, table=True):
    __tablename__ = "blogger_accounts"
    __table_args__ = {"schema": "ops"}

    email: str = Field(sa_type=Text)
    name: str = Field(sa_type=Text)
    bio: str | None = Field(default=None, sa_type=Text)
    password_hash: str | None = Field(default=None, sa_type=Text)
    active: bool | None = None
    invite_token: str | None = Field(default=None, sa_type=Text)
    token_expires: str | None = Field(default=None, sa_type=Text)
    created_at_attr: str | None = Field(default=None, sa_type=Text)


class AiCitation(TimestampMixin, SQLModel, table=True):
    __tablename__ = "ai_citations"
    __table_args__ = (
        Index("ix_ai_citations_idx_run_id", "run_id"),
        Index("ix_ai_citations_idx_date", "date"),
        Index("ix_ai_citations_idx_engine", "engine"),
        {"schema": "ops"},
    )

    run_id: str = Field(sa_type=Text)
    date: str = Field(sa_type=Text)
    week_num: int = Field(sa_type=BigInteger)
    engine: str = Field(sa_type=Text)
    engine_label: str = Field(sa_type=Text)
    query_id: str = Field(sa_type=Text)
    query_text: str = Field(sa_type=Text)
    cited: str = Field(sa_type=Text)
    position: int | None = Field(default=None, sa_type=BigInteger)
    cited_page: str | None = Field(default=None, sa_type=Text)
    quote_type: str | None = Field(default=None, sa_type=Text)
    competitors: str | None = Field(default=None, sa_type=Text)
    raw_citations: str | None = Field(default=None, sa_type=Text)
    content_snippet: str | None = Field(default=None, sa_type=Text)
    duration_ms: int | None = Field(default=None, sa_type=BigInteger)
    error_message: str | None = Field(default=None, sa_type=Text)


class SeoRun(SQLModel, table=True):
    __tablename__ = "seo_runs"
    __table_args__ = (
        CheckConstraint(_in("scope", SEO_SCOPES), name="seo_runs_scope_check"),
        CheckConstraint(_in("verdict_code", SEO_VERDICT_CODES), name="seo_runs_verdict_code_check"),
        Index("seo_runs_run_at", text("run_at desc")),
        {"schema": "ops"},
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("ops.uuid_generate_v7()")},
    )
    run_at: datetime = Field(sa_type=DateTime(timezone=True))
    site: str = Field(sa_type=Text)
    scope: str = Field(sa_type=Text)
    dry_run: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    inspected: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    errors: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    sitemap_url_count: int = Field(default=0, sa_column_kwargs={"server_default": text("0")})
    sitemap_urls: list[str] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    verdict_code: str = Field(sa_type=Text)
    summary: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": text("now()")},
    )


class SeoInspection(SQLModel, table=True):
    __tablename__ = "seo_inspections"
    __table_args__ = (
        CheckConstraint(_in("bucket", SEO_BUCKETS), name="seo_inspections_bucket_check"),
        UniqueConstraint("run_id", "url"),
        Index("seo_inspections_url_run_at", "url", text("run_at desc")),
        Index("seo_inspections_run_bucket", "run_id", "bucket"),
        {"schema": "ops"},
    )

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("ops.uuid_generate_v7()")},
    )
    run_id: uuid.UUID = Field(
        sa_column=Column(
            UUID(as_uuid=True),
            ForeignKey("ops.seo_runs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    run_at: datetime = Field(sa_type=DateTime(timezone=True))
    url: str = Field(sa_type=Text)
    path: str = Field(sa_type=Text)
    watchlist: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    in_sitemap: bool = Field(default=False, sa_column_kwargs={"server_default": text("false")})
    bucket: str = Field(sa_type=Text)
    coverage_state: str | None = Field(default=None, sa_type=Text)
    verdict: str | None = Field(default=None, sa_type=Text)
    indexing_state: str | None = Field(default=None, sa_type=Text)
    last_crawl_time: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    google_canonical: str | None = Field(default=None, sa_type=Text)
    user_canonical: str | None = Field(default=None, sa_type=Text)
    robots_txt_state: str | None = Field(default=None, sa_type=Text)
    page_fetch_state: str | None = Field(default=None, sa_type=Text)
    referring_urls: int | None = Field(default=None, sa_type=Integer)
    requested_indexing_at: date | None = Field(default=None, sa_type=Date)
    clicks_28d: int | None = Field(default=None, sa_type=Integer)
    impressions_28d: int | None = Field(default=None, sa_type=Integer)
    error: str | None = Field(default=None, sa_type=Text)
    raw: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": text("now()")},
    )


class SeoIndexRequest(SQLModel, table=True):
    """Request-indexing ledger: one row per URL the GSC button was pressed for."""

    __tablename__ = "seo_index_requests"
    __table_args__ = {"schema": "ops"}

    url: str = Field(sa_column=Column(Text, primary_key=True))
    requested_at: date = Field(sa_type=Date)
    note: str | None = Field(default=None, sa_type=Text)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": text("now()")},
    )


class AdminTask(TimestampMixin, SQLModel, table=True):
    """One admin-triggered background run. `result` is the JSON the old synchronous route
    returned, so the "Run now" buttons render exactly what they used to."""

    __tablename__ = "admin_tasks"
    __table_args__ = (
        CheckConstraint("status in ('running', 'done', 'failed')", name="admin_tasks_status_check"),
        Index("ix_admin_tasks_kind_created", "kind", "created_at"),
        {"schema": "ops"},
    )

    kind: str = Field(sa_type=Text)
    status: str = Field(default="running", sa_type=Text)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    started_by: uuid.UUID | None = Field(default=None)
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
