"""Tables written by the public forms: contact, subscribe, unsubscribe, survey,
template downloads and the white-paper (guide) gate.

Columns mirror `_backup/supabase/migrations/0001_initial_schema.sql` exactly, including the
renamed `created_at_attr` columns that `lib/db/supabase.ts` mapped the application's
`created_at` field onto. `created_at` / `updated_at` themselves stay database-managed.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

SUPPRESSED_STATUSES = ("unsubscribed", "bounced", "complained")

_TZ = DateTime(timezone=True)


def _nullable_ts() -> Any:
    """A nullable `timestamptz` column. Returns the FieldInfo, not a datetime."""
    return Field(default=None, sa_type=_TZ, nullable=True)


class Lead(TimestampMixin, SQLModel, table=True):
    __tablename__ = "leads"
    __table_args__ = {"schema": "ops"}

    name: str
    email: str
    phone: str | None = None
    company: str
    industry: str | None = None
    company_size: str | None = None
    source: str
    issue_summary: str | None = None
    preferred_contact: str | None = None
    preferred_time: str | None = None
    consent_version: str | None = None
    risk_level: str | None = None
    created_at_attr: datetime | None = _nullable_ts()
    ip_address: str | None = None
    city: str | None = None
    country: str | None = None
    region: str | None = None


class Subscriber(TimestampMixin, SQLModel, table=True):
    __tablename__ = "subscribers"
    __table_args__ = {"schema": "ops"}

    name: str
    email: str
    industry: str | None = None
    frequency: str | None = None
    consent_version: str | None = None
    user_agent: str | None = None
    created_at_attr: datetime | None = _nullable_ts()
    ip_address: str | None = None
    city: str | None = None
    country: str | None = None
    region: str | None = None
    # check (consent_source in ('manual','assessment_form','intro_email_one_click',
    #                           'report_email_cta','admin_added'))
    consent_source: str
    consent_timestamp: datetime | None = _nullable_ts()
    unsubscribe_token: str | None = Field(default=None, unique=True)
    # check (status in ('active','unsubscribed','bounced','complained'))
    status: str
    unsubscribed_at: datetime | None = _nullable_ts()


class Download(TimestampMixin, SQLModel, table=True):
    """White-paper (DPDPA Guide) gate submissions — `ops.downloads`."""

    __tablename__ = "downloads"
    __table_args__ = {"schema": "ops"}

    name: str
    email: str
    company: str
    industry: str | None = None
    company_size: str | None = None
    consent_email: bool | None = None
    consent_phone: bool | None = None
    consent_webinars: bool | None = None
    privacy_version: str | None = None
    downloaded_at: datetime | None = _nullable_ts()
    ip_address: str | None = None
    city: str | None = None
    country: str | None = None
    region: str | None = None
    phone: str | None = None
    language: str | None = None


class ConsentLog(TimestampMixin, SQLModel, table=True):
    """Append-only consent audit trail. `timestamp` / `withdrawn_at` are text columns in
    the DDL (Appwrite stored ISO strings) — kept as text so exported rows still match."""

    __tablename__ = "consent_log"
    __table_args__ = {"schema": "ops"}

    email: str
    name: str | None = None
    source: str
    consent_type: str
    privacy_version: str
    ip_address: str | None = None
    user_agent: str | None = None
    city: str | None = None
    country: str | None = None
    region: str | None = None
    timestamp: str
    withdrawn_at: str | None = None
    consent_value: bool


class SurveyResponse(TimestampMixin, SQLModel, table=True):
    __tablename__ = "survey_responses"
    __table_args__ = {"schema": "ops"}

    role: str | None = None
    employee_size: str | None = None
    sector: str | None = None
    operating_footprint: str | None = None
    state_ut: str | None = None
    city: str | None = None
    digital_personal_data: str | None = None
    data_types: str | None = None
    data_storage: str | None = None
    controls_in_place: str | None = None
    readiness_self_view: str | None = None
    biggest_blocker: str | None = None
    most_helpful_resource: str | None = None
    score_band: str | None = None
    name: str | None = None
    business_name: str | None = None
    work_email: str | None = None
    mobile_number: str | None = None
    contact_preference: str | None = None
    consent_version: str | None = None
    consent_timestamp: str | None = None
    created_at_attr: str | None = None
    score: int | None = None
    wants_report: bool | None = None
    consent_followup: bool | None = None
    consent_given: bool | None = None


class TemplateDownload(TimestampMixin, SQLModel, table=True):
    __tablename__ = "template_downloads"
    __table_args__ = {"schema": "ops"}

    business_name: str
    employees: str | None = None
    phone: str
    email: str | None = None
    template_name: str | None = None
    consent_contact: bool | None = None
    report_token: str | None = None
    ip_address: str | None = None
    city: str | None = None
    country: str | None = None
    created_at_attr: str | None = None
    contact_name: str
    source: str | None = None
