"""Tables owned by the editorial module (daily briefings + the verified blog).

Columns mirror `_backup/supabase/migrations/0001_initial_schema.sql` exactly:

- `app.briefings_meta` (the old Appwrite collection `briefings`). The application field
  `created_at` lives in the renamed column `created_at_attr` (see `lib/db/supabase.ts`
  RENAMES); the mixin's `created_at` / `updated_at` stay database-managed.
- `ops.blog_posts` (collection `blog_posts`). No renamed columns.

`ops.ai_citations` is also listed under editorial in the task brief, but the admin module
already models it (`app.models.admin.AiCitation`, written by its AEO job). Declaring it
twice would register the same table twice on the shared metadata, so it is not repeated here.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Text
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

_TZ = DateTime(timezone=True)

PUBLISHED_BRIEFING_STATUSES = ("approved", "sent")


class Briefing(TimestampMixin, SQLModel, table=True):
    __tablename__ = "briefings_meta"
    __table_args__ = (
        Index("ix_briefings_meta_slug_idx", "slug"),
        {"schema": "app"},
    )

    title: str = Field(sa_type=Text)
    slug: str = Field(sa_type=Text)
    summary: str | None = Field(default=None, sa_type=Text)
    why_it_matters: str | None = Field(default=None, sa_type=Text)
    action_checklist: str | None = Field(default=None, sa_type=Text)
    status: str | None = Field(default=None, sa_type=Text)
    approval_token: str | None = Field(default=None, sa_type=Text)
    scheduled_for: datetime | None = Field(default=None, sa_type=_TZ, nullable=True)
    sent_at: datetime | None = Field(default=None, sa_type=_TZ, nullable=True)
    created_at_attr: datetime | None = Field(default=None, sa_type=_TZ, nullable=True)
    subscriber_count: int | None = Field(default=None, sa_type=BigInteger)
    excerpt: str | None = Field(default=None, sa_type=Text)
    category: str | None = Field(default=None, sa_type=Text)
    tags: str | None = Field(default=None, sa_type=Text)
    industries: str | None = Field(default=None, sa_type=Text)
    author: str | None = Field(default=None, sa_type=Text)
    published_at: str | None = Field(default=None, sa_type=Text)
    read_time: int | None = Field(default=None, sa_type=BigInteger)
    featured: bool | None = None
    infographic_base64: str | None = Field(default=None, sa_type=Text)
    outreach_used_at: datetime | None = Field(default=None, sa_type=_TZ, nullable=True)


class BlogPost(TimestampMixin, SQLModel, table=True):
    __tablename__ = "blog_posts"
    __table_args__ = {"schema": "ops"}

    title: str = Field(sa_type=Text)
    slug: str = Field(sa_type=Text)
    excerpt: str = Field(sa_type=Text)
    lane: str = Field(sa_type=Text)
    author: str = Field(sa_type=Text)
    tags: str | None = Field(default=None, sa_type=Text)
    industries: str | None = Field(default=None, sa_type=Text)
    validated_at: str | None = Field(default=None, sa_type=Text)
    published_at: str | None = Field(default=None, sa_type=Text)
    section_what_changed: str | None = Field(default=None, sa_type=Text)
    status: str | None = Field(default=None, sa_type=Text)
    section_law_says: str | None = Field(default=None, sa_type=Text)
    read_time: int | None = Field(default=None, sa_type=BigInteger)
    score_legal_accuracy: int | None = Field(default=None, sa_type=BigInteger)
    score_primary_source: int | None = Field(default=None, sa_type=BigInteger)
    score_currency: int | None = Field(default=None, sa_type=BigInteger)
    score_scope: int | None = Field(default=None, sa_type=BigInteger)
    score_operational: int | None = Field(default=None, sa_type=BigInteger)
    validation_score: int | None = Field(default=None, sa_type=BigInteger)
    featured: bool | None = None
    sections_json: str | None = Field(default=None, sa_type=Text)
    infographic_url: str | None = Field(default=None, sa_type=Text)
