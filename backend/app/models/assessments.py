"""DPDPA readiness assessments — `app.assessments`.

Columns mirror `_backup/supabase/migrations/0001_initial_schema.sql` exactly, including the
Appwrite-era attribute `created_at` that had to be renamed `created_at_attr` in
Postgres (see `lib/db/supabase.ts` RENAMES); the API keeps calling it `created_at`.

`ops.consent_log` and `ops.subscribers` are written by this module too, but their
SQLModel classes belong to the forms module — writes here go through plain SQL in
`app.crud.assessments` so two builders never declare the same table.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin


class Assessment(TimestampMixin, SQLModel, table=True):
    __tablename__ = "assessments"
    __table_args__ = {"schema": "app"}

    # ── Legacy industry-assessment fields ────────────────────────────────
    email: str
    industry: str
    risk_level: str
    applicability_score: float | None = None
    maturity_score: float | None = None
    risk_score: float | None = None
    urgency_score: float | None = None
    overall_score: float | None = None

    # ── General / pack assessment fields ─────────────────────────────────
    raw_score: int | None = None
    final_score: int | None = None
    data_exposure: int | None = None
    control_maturity: int | None = None
    operational_readiness: int | None = None
    consent_report: bool | None = None
    consent_newsletter: bool | None = None
    consent_followup: bool | None = None
    verdict_band: str | None = None
    report_type: str | None = None
    name: str | None = None
    business_name: str | None = None
    mobile: str | None = None
    q11_blocker: str | None = None
    q12_resource: str | None = None
    red_flags_json: str | None = None
    immediate_actions_json: str | None = None
    thirty_day_actions_json: str | None = None
    answers_json: str | None = None
    category_scores_json: str | None = None

    # ── Report delivery ──────────────────────────────────────────────────
    # Both are `text` in the DDL: the route stored ISO strings, not timestamps.
    report_token: str | None = Field(default=None, index=True)
    report_token_expires_at: str | None = None
    email_sent_at: str | None = None
    email_sent_by: str | None = None

    # ── Geo / session ────────────────────────────────────────────────────
    # `created_at` in the submitted document; renamed in Postgres.
    created_at_attr: datetime | None = Field(
        default=None, sa_column=Column("created_at_attr", DateTime(timezone=True), nullable=True)
    )
    ip_address: str | None = None
    city: str | None = None
    country: str | None = None
    region: str | None = None


__all__ = ["Assessment"]
