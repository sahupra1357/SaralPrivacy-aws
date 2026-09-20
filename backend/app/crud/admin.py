"""Queries for the admin module. No business logic, no HTTP concepts.

Tables other modules own are read with parameterised `text()` SQL so this module never
imports their models. Identifiers in that SQL come only from the fixed `TARGETS`
allowlist below; every value is a bound parameter.
"""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, text
from sqlmodel import Session, col, select

from app.models.admin import (
    AdminTask,
    AiCitation,
    BloggerAccount,
    SeoIndexRequest,
    SeoInspection,
    SeoRun,
)

# ── Generic collection reader (/admin/data, dashboard) ──────────────────────
# Appwrite collection name → Postgres table (lib/db/supabase.ts TARGETS).
TARGETS: dict[str, tuple[str, str]] = {
    "leads": ("ops", "leads"),
    "subscribers": ("ops", "subscribers"),
    "downloads": ("ops", "downloads"),
    "template_downloads": ("ops", "template_downloads"),
    "survey_responses": ("ops", "survey_responses"),
    "consent_log": ("ops", "consent_log"),
    "email_send_log": ("ops", "email_send_log"),
    "outreach_contacts": ("ops", "outreach_contacts"),
    "ai_citations": ("ops", "ai_citations"),
    "blog_posts": ("ops", "blog_posts"),
    "blogger_accounts": ("ops", "blogger_accounts"),
    "chat_feedback": ("ops", "chat_feedback"),
    "assessments": ("app", "assessments"),
    "briefings": ("app", "briefings_meta"),
    "notice_captures": ("app", "notice_captures"),
    "notice_events": ("app", "notice_events"),
    "notice_runs": ("app", "notice_runs"),
    "business_profiles": ("app", "business_profiles"),
    "dsar_requests": ("app", "dsar_requests"),
}

# The /api/admin/data allowlist was Object.values(COLLECTIONS) — the same 19 names.
ALLOWED_COLLECTIONS: frozenset[str] = frozenset(TARGETS)

# Appwrite attribute names that collided with the standard columns (lib/db RENAMES).
RENAMES: dict[str, dict[str, str]] = {
    "leads": {"created_at": "created_at_attr"},
    "subscribers": {"created_at": "created_at_attr"},
    "assessments": {"created_at": "created_at_attr"},
    "briefings": {"created_at": "created_at_attr"},
    "survey_responses": {"created_at": "created_at_attr"},
    "blogger_accounts": {"created_at": "created_at_attr"},
    "template_downloads": {"created_at": "created_at_attr"},
    "outreach_contacts": {"created_at": "created_at_attr"},
    "email_send_log": {"updated_at": "updated_at_attr"},
    "notice_captures": {"created_at": "created_at_attr"},
    "notice_events": {"created_at": "created_at_attr"},
}

# Filters the admin pages send. Column names are fixed here, never taken from the request.
FILTER_COLUMNS = ("source", "status", "risk_level")


def to_column(collection: str, field: str) -> str:
    return RENAMES.get(collection, {}).get(field, field)


def json_safe(value: Any) -> Any:
    """JSON-safe values for the admin pages. Timestamps use JavaScript's toISOString()
    shape (UTC, milliseconds, trailing Z) — the exact strings the old app stored — so
    pages that sort or compare these strings behave as before."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        utc = value.astimezone(UTC)
        return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def row_to_doc(collection: str, row: dict[str, Any]) -> dict[str, Any]:
    """Postgres row → the Appwrite-era document the admin pages read.

    Renamed columns map back to their attribute names (`created_at_attr` → `created_at`);
    as in lib/db, the attribute value wins over the system column of the same name. The
    system columns are also exposed as `$createdAt` / `$updatedAt`, the id as `id` + `$id`.
    """
    inverse = {v: k for k, v in RENAMES.get(collection, {}).items()}
    doc: dict[str, Any] = {}
    renamed_targets = set(inverse.values())
    for key, value in row.items():
        if key in renamed_targets:
            # The system column is shadowed by the renamed attribute when that exists.
            doc.setdefault(key, json_safe(value))
            continue
        doc[inverse.get(key, key)] = json_safe(value)
    doc["id"] = str(row["id"])
    doc["$id"] = str(row["id"])
    doc["$createdAt"] = json_safe(row.get("created_at"))
    doc["$updatedAt"] = json_safe(row.get("updated_at"))
    return doc


def _where(collection: str, filters: dict[str, str]) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for field in FILTER_COLUMNS:
        if field in filters and filters[field] is not None:
            column = to_column(collection, field)
            clauses.append(f'"{column}" = :f_{field}')
            params[f"f_{field}"] = filters[field]
    return (" where " + " and ".join(clauses)) if clauses else "", params


def query_documents(
    session: Session, collection: str, *, filters: dict[str, str] | None = None, limit: int = 200
) -> tuple[list[dict[str, Any]], int]:
    """Newest first. `collection` must be in ALLOWED_COLLECTIONS (raises KeyError otherwise)."""
    schema, table = TARGETS[collection]
    where, params = _where(collection, filters or {})
    rows = session.execute(
        text(f'select * from "{schema}"."{table}"{where} order by created_at desc limit :limit'),
        {**params, "limit": max(0, limit)},
    ).mappings()
    docs = [row_to_doc(collection, dict(r)) for r in rows]
    total = count_documents(session, collection, filters=filters)
    return docs, total


def count_documents(
    session: Session, collection: str, *, filters: dict[str, str] | None = None
) -> int:
    schema, table = TARGETS[collection]
    where, params = _where(collection, filters or {})
    value = session.execute(
        text(f'select count(*) from "{schema}"."{table}"{where}'), params
    ).scalar()
    return int(value or 0)


# ── ops.blogger_accounts ────────────────────────────────────────────────────
def list_bloggers(session: Session, limit: int = 100) -> list[BloggerAccount]:
    stmt = select(BloggerAccount).order_by(col(BloggerAccount.created_at).desc()).limit(limit)
    return list(session.exec(stmt).all())


def get_blogger_by_email(session: Session, email: str) -> BloggerAccount | None:
    stmt = select(BloggerAccount).where(
        func.lower(col(BloggerAccount.email)) == email.strip().lower()
    )
    return session.exec(stmt).first()


def resolve_blogger(session: Session, blogger_id: str) -> BloggerAccount | None:
    """Accepts the row uuid or an Appwrite-era `legacy_id` (old admin links)."""
    try:
        return session.get(BloggerAccount, uuid.UUID(blogger_id))
    except ValueError:
        stmt = select(BloggerAccount).where(BloggerAccount.legacy_id == blogger_id)
        return session.exec(stmt).first()


def create_blogger(
    session: Session, *, email: str, name: str, bio: str, created_at_attr: str
) -> BloggerAccount:
    row = BloggerAccount(
        email=email,
        name=name,
        bio=bio,
        password_hash="",
        active=False,
        invite_token="pending",
        token_expires="",
        created_at_attr=created_at_attr,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def set_blogger_active(session: Session, row: BloggerAccount, active: bool) -> BloggerAccount:
    row.active = active
    row.invite_token = ""
    row.updated_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def delete_blogger(session: Session, row: BloggerAccount) -> None:
    session.delete(row)
    session.commit()


_DELETE_USER_SQL = (
    text("delete from app.invite_tokens where user_id = :uid"),
    text("delete from app.sessions where user_id = :uid"),
    text("delete from app.users where id = :uid"),
)


def delete_auth_user(session: Session, user_id: uuid.UUID) -> None:
    """The directory row's account in app.users, with the rows that reference it.
    (lib/auth/adminAuth.deleteAuthUserByEmail had no backend replacement by design.)"""
    for stmt in _DELETE_USER_SQL:
        session.execute(stmt, {"uid": user_id})
    session.commit()


# ── app.assessments (assessments module's table — raw SQL, no model import) ─
_ASSESSMENT_BY_ID = text("select * from app.assessments where id = :id limit 1")
_ASSESSMENT_BY_LEGACY = text("select * from app.assessments where legacy_id = :id limit 1")
_ASSESSMENT_MARK_SENT = text(
    "update app.assessments set email_sent_at = :sent_at, email_sent_by = :sent_by, "
    "updated_at = now() where id = :id"
)


def get_assessment(session: Session, assessment_id: str) -> dict[str, Any] | None:
    try:
        stmt, key = _ASSESSMENT_BY_ID, str(uuid.UUID(assessment_id))
    except ValueError:
        stmt, key = _ASSESSMENT_BY_LEGACY, assessment_id
    row = session.execute(stmt, {"id": key}).mappings().first()
    return dict(row) if row else None


def mark_assessment_sent(
    session: Session, assessment_id: uuid.UUID, sent_at: str, sent_by: str
) -> None:
    session.execute(
        _ASSESSMENT_MARK_SENT, {"id": assessment_id, "sent_at": sent_at, "sent_by": sent_by}
    )
    session.commit()


# ── ops.ai_citations ────────────────────────────────────────────────────────
def insert_citation(session: Session, row: AiCitation) -> AiCitation:
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── ops.seo_* ───────────────────────────────────────────────────────────────
def latest_seo_run(session: Session) -> SeoRun | None:
    stmt = (
        select(SeoRun)
        .where(col(SeoRun.dry_run).is_(False))
        .order_by(col(SeoRun.run_at).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def list_seo_runs(session: Session, limit: int = 12) -> list[SeoRun]:
    stmt = (
        select(SeoRun)
        .where(col(SeoRun.dry_run).is_(False))
        .order_by(col(SeoRun.run_at).desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def list_seo_inspections(
    session: Session, run_id: uuid.UUID, *, order_by: str = "path"
) -> list[SeoInspection]:
    order = col(SeoInspection.url) if order_by == "url" else col(SeoInspection.path)
    stmt = select(SeoInspection).where(SeoInspection.run_id == run_id).order_by(order.asc())
    return list(session.exec(stmt).all())


def insert_seo_run(
    session: Session, run: SeoRun, inspections: list[SeoInspection], page: int = 500
) -> None:
    """Run row first (FK), then the inspections in pages, as lib/seo/db.ts persisted them."""
    session.add(run)
    session.commit()
    for i in range(0, len(inspections), page):
        session.add_all(inspections[i : i + page])
        session.commit()


def list_index_requests(session: Session, limit: int = 1000) -> list[SeoIndexRequest]:
    stmt = (
        select(SeoIndexRequest)
        .order_by(col(SeoIndexRequest.requested_at).desc(), col(SeoIndexRequest.url).asc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def upsert_index_request(
    session: Session, url: str, requested_at: date, note: str | None
) -> SeoIndexRequest:
    row = session.get(SeoIndexRequest, url)
    if row is None:
        row = SeoIndexRequest(url=url, requested_at=requested_at, note=note)
    else:
        row.requested_at = requested_at
        row.note = note
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── ops.admin_tasks ─────────────────────────────────────────────────────────
def create_task(session: Session, kind: str, started_by: uuid.UUID | None) -> AdminTask:
    row = AdminTask(kind=kind, status="running", started_by=started_by)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_task(session: Session, task_id: uuid.UUID) -> AdminTask | None:
    return session.get(AdminTask, task_id)


def finish_task(
    session: Session, task_id: uuid.UUID, *, ok: bool, result: dict[str, Any]
) -> AdminTask | None:
    row = session.get(AdminTask, task_id)
    if row is None:
        return None
    row.status = "done" if ok else "failed"
    # Round-trip through JSON so the JSONB column only ever sees plain types.
    row.result = json.loads(json.dumps(json_safe(result)))
    now = datetime.now(UTC)
    row.finished_at = now
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
