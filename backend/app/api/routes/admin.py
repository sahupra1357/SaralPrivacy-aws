"""Admin back office: the allowlisted data reader, the dashboard, blogger management,
"send report", and the status of background runs.

Every route is admin-only (`require_role("admin")`), as every TypeScript route was.
User-facing messages are the TypeScript strings, verbatim (docs/build/inventory/admin.md).
"""

import json
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.api.deps import SessionDep, require_role
from app.api.routes.forms import render_survey_report
from app.api.routes.login import INVITE_TTL_HOURS, send_link_email, set_password_url
from app.core.config import settings
from app.core.db import engine
from app.crud import admin as crud
from app.crud import auth as auth_crud
from app.models.admin import AdminTask, BloggerAccount
from app.services import email as email_service

log = logging.getLogger(__name__)

AdminUser = Annotated[Any, Depends(require_role("admin"))]

router = APIRouter(prefix="/admin", tags=["admin"])

INVALID_COLLECTION = "Invalid collection"
FETCH_FAILED = "Failed to fetch data."
EMAIL_AND_NAME_REQUIRED = "Email and name are required."
BLOGGER_EXISTS = "A blogger with this email already exists."
INVITE_FAILED = "Could not create the invite."
ASSESSMENT_ID_REQUIRED = "assessmentId is required"
ASSESSMENT_NOT_FOUND = "Assessment not found"
ASSESSMENT_NO_EMAIL = "Assessment has no email address"
EMAIL_FAILED = "Email failed to send"
TASK_NOT_FOUND = "Task not found"

DATA_DEFAULT_LIMIT = 200
DATA_MAX_LIMIT = 500

BAND_DESCRIPTIONS: dict[str, str] = {
    "Not Started": "Your business shows high exposure or very weak controls. Immediate action is needed on the fundamentals before your risk compounds.",
    "Early Stage": "You have some awareness of DPDPA obligations but important gaps remain. A focused 30-day effort will close most of the critical gaps.",
    "Building Foundations": "Basic elements exist but are not applied consistently across your business. Focus on ownership, consent, and operational readiness.",
    "Progressing Well": "Good momentum and some operational maturity. Tighten documentation, vendor controls, and test your incident response.",
    "Operationally Strong": "Strong readiness signals across most areas. Your controls are well above average for Indian MSMEs.",
}

# camelCase keys stored in category_scores_json → the keys render_survey_report reads.
CATEGORY_KEYS: dict[str, str] = {
    "noticeConsent": "notice_consent",
    "accessControl": "access_control",
    "retentionDeletion": "retention_deletion",
    "ownershipGovernance": "ownership_governance",
    "vendorPartnerRisk": "vendor_partner_risk",
    "incidentReadiness": "incident_readiness",
}


def iso_z(dt: datetime | None = None) -> str:
    """`new Date().toISOString()` — millisecond precision, `Z` suffix."""
    value = (dt or datetime.now(UTC)).astimezone(UTC)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


# ── Background runs (AEO panel, SEO inspection) ─────────────────────────────
def task_session() -> AbstractContextManager[Session]:
    """A fresh session for work that outlives the request. Tests patch this."""
    return Session(engine)


TaskWork = Callable[[Session], tuple[bool, dict[str, Any]]]


def _run_task(task_id: uuid.UUID, work: TaskWork) -> None:
    with task_session() as session:
        try:
            ok, result = work(session)
        except Exception as exc:  # noqa: BLE001 — the task row must always be closed
            log.exception("admin task %s crashed", task_id)
            session.rollback()
            ok, result = False, {"ok": False, "error": str(exc)}
        crud.finish_task(session, task_id, ok=ok, result=result)


def start_task(
    session: Session, background: BackgroundTasks, *, kind: str, actor: Any, work: TaskWork
) -> dict[str, Any]:
    """Record a `running` task, schedule the work after the response, answer 202 at once.
    The old routes held the request open for up to 300 s; the buttons now poll
    `GET /admin/tasks/{id}` and render the same payload when it lands."""
    task = crud.create_task(session, kind, getattr(actor, "id", None))
    background.add_task(_run_task, task.id, work)
    return {"ok": True, "task_id": str(task.id), "status": task.status}


class TaskOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    result: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


@router.get("/tasks/{task_id}", response_model=TaskOut)
def read_task(task_id: uuid.UUID, session: SessionDep, _admin: AdminUser) -> AdminTask:
    task = crud.get_task(session, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, TASK_NOT_FOUND)
    return task


# ── GET /admin/data — allowlisted generic reader ────────────────────────────
def _parse_limit(raw: str | None) -> int:
    try:
        value = int(str(raw).strip()) if raw not in (None, "") else DATA_DEFAULT_LIMIT
    except ValueError:
        value = DATA_DEFAULT_LIMIT
    return max(0, min(value, DATA_MAX_LIMIT))


@router.get("/data")
def read_data(
    session: SessionDep,
    _admin: AdminUser,
    collection: str = "",
    limit: str | None = None,
    source: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
) -> dict[str, Any]:
    if collection not in crud.ALLOWED_COLLECTIONS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_COLLECTION)

    filters: dict[str, str] = {}
    if source:
        filters["source"] = source
    if status_:
        filters["status"] = status_
    try:
        docs, total = crud.query_documents(
            session, collection, filters=filters, limit=_parse_limit(limit)
        )
    except Exception:  # noqa: BLE001 — same generic 500 the TypeScript returned
        session.rollback()
        log.exception("Admin data fetch [%s]", collection)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, FETCH_FAILED)
    return {"documents": docs, "total": total}


# ── GET /admin/dashboard — what app/(backoffice)/admin/page.tsx rendered ────
DASHBOARD_COUNTS = (
    "leads",
    "subscribers",
    "downloads",
    "assessments",
    "survey_responses",
    "briefings",
)
DASHBOARD_RECENT = {
    "leads": 8,
    "subscribers": 8,
    "downloads": 8,
    "survey_responses": 8,
    "assessments": 8,
    "briefings": 5,
}


def _safe_count(session: Session, collection: str, filters: dict[str, str] | None = None) -> int:
    try:
        return crud.count_documents(session, collection, filters=filters)
    except Exception:  # noqa: BLE001 — a missing table shows 0, as the page did
        session.rollback()
        log.exception("dashboard count failed [%s]", collection)
        return 0


def _safe_recent(session: Session, collection: str, limit: int) -> list[dict[str, Any]]:
    try:
        docs, _ = crud.query_documents(session, collection, limit=limit)
        return docs
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("dashboard recent failed [%s]", collection)
        return []


@router.get("/dashboard")
def read_dashboard(session: SessionDep, _admin: AdminUser) -> dict[str, Any]:
    counts = {c: _safe_count(session, c) for c in DASHBOARD_COUNTS}
    recent = {c: _safe_recent(session, c, n) for c, n in DASHBOARD_RECENT.items()}
    risk = {
        level: _safe_count(session, "assessments", {"risk_level": level})
        for level in ("green", "amber", "red")
    }
    risk["total"] = counts["assessments"]
    return {"counts": counts, "recent": recent, "risk": risk}


# ── Bloggers ────────────────────────────────────────────────────────────────
def blogger_doc(row: BloggerAccount) -> dict[str, Any]:
    return crud.row_to_doc("blogger_accounts", row.model_dump())


class BloggerIn(BaseModel):
    email: Any = None
    name: Any = None
    bio: Any = None


class BloggerPatchIn(BaseModel):
    active: Any = None


@router.get("/bloggers")
def list_bloggers(session: SessionDep, _admin: AdminUser) -> dict[str, Any]:
    try:
        rows = crud.list_bloggers(session, limit=100)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    return {"bloggers": [blogger_doc(r) for r in rows]}


@router.post("/bloggers")
def invite_blogger(body: BloggerIn, session: SessionDep, actor: AdminUser) -> dict[str, Any]:
    if not body.email or not body.name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, EMAIL_AND_NAME_REQUIRED)

    email = str(body.email).strip().lower()
    name = str(body.name).strip()
    bio = str(body.bio or "").strip()

    # Duplicate: a directory row OR an account with this email.
    try:
        exists = crud.get_blogger_by_email(session, email) is not None or (
            auth_crud.get_user_by_email(session, email) is not None
        )
    except Exception:  # noqa: BLE001 — the TypeScript logged and carried on
        session.rollback()
        log.exception("[bloggers] duplicate check failed")
        exists = False
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, BLOGGER_EXISTS)

    try:
        user = auth_crud.create_user(session, email=email, role="blogger", display_name=name)
        token, _row = auth_crud.create_invite_token(session, user.id, "invite", INVITE_TTL_HOURS)
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("[bloggers] createInvite failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, INVITE_FAILED)
    invite_url = set_password_url(token, "invite")

    row = crud.create_blogger(session, email=email, name=name, bio=bio, created_at_attr=iso_z())

    # Best effort — the URL comes back regardless so the admin can share it by hand.
    sent, error = send_link_email(
        to=email, name=name, role="blogger", kind="invite", url=invite_url, valid_for="24 hours"
    )
    auth_crud.audit(
        session, "invite", actor_user_id=actor.id, target=str(user.id), details={"role": "blogger"}
    )
    return {
        "success": True,
        "id": str(row.id),
        "inviteUrl": invite_url,
        "emailSent": sent,
        "emailError": error,
    }


@router.patch("/bloggers/{blogger_id}")
def set_blogger_active(
    blogger_id: str, body: BloggerPatchIn, session: SessionDep, actor: AdminUser
) -> dict[str, Any]:
    active = body.active is True
    try:
        row = crud.resolve_blogger(session, blogger_id)
        if row is None:
            return {"success": True}
        crud.set_blogger_active(session, row, active)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("[bloggers] PATCH failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))

    # Close the account side too. Login already gates on `active`; belt and braces.
    try:
        user = auth_crud.get_user_by_email(session, row.email)
        if user is not None:
            auth_crud.update_user(session, user, is_active=active)
            if not active:
                auth_crud.revoke_all_sessions(session, user.id)
        auth_crud.audit(
            session,
            "blogger.update",
            actor_user_id=actor.id,
            target=str(row.id),
            details={"active": active},
        )
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("[bloggers] ban/unban failed")
    return {"success": True}


@router.delete("/bloggers/{blogger_id}")
def delete_blogger(blogger_id: str, session: SessionDep, actor: AdminUser) -> dict[str, Any]:
    try:
        row = crud.resolve_blogger(session, blogger_id)
        if row is None:
            return {"success": True}
        user = auth_crud.get_user_by_email(session, row.email) if row.email else None
        if user is not None:
            crud.delete_auth_user(session, user.id)
        target = str(row.id)
        crud.delete_blogger(session, row)
        auth_crud.audit(session, "blogger.delete", actor_user_id=actor.id, target=target)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("[bloggers] DELETE failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    return {"success": True}


# ── POST /admin/send-report ─────────────────────────────────────────────────
class AnswerRow(BaseModel):
    question: str
    answer: str


class SendReportIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    assessment_id: Any = Field(default=None, alias="assessmentId")
    # Rebuilt in the browser from lib/data/dpdpa-assessment.ts QUESTIONS (pure data that
    # stays in the frontend) — the route used to import it server-side.
    answer_summary: list[AnswerRow] = Field(default_factory=list, alias="answerSummary")


def _json_field(raw: Any, fallback: Any) -> Any:
    try:
        value = json.loads(raw) if raw else fallback
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def build_report_data(doc: dict[str, Any], answer_summary: list[AnswerRow]) -> dict[str, Any]:
    categories = _json_field(doc.get("category_scores_json"), {})
    band_key = doc.get("verdict_band")
    return {
        "email": doc["email"],
        "name": doc.get("name") or "",
        "business_name": doc.get("business_name") or "",
        "score": doc.get("final_score") or doc.get("overall_score") or 0,
        "band": band_key or "Early Stage",
        "summary": BAND_DESCRIPTIONS.get(str(band_key), ""),
        "recommendations": _json_field(doc.get("immediate_actions_json"), []),
        "risk_flags": _json_field(doc.get("red_flags_json"), []),
        "answer_summary": [row.model_dump() for row in answer_summary],
        "report_token": doc.get("report_token") or "",
        "category_scores": {
            snake: (categories.get(camel) if categories.get(camel) is not None else 0)
            for camel, snake in CATEGORY_KEYS.items()
        },
    }


@router.post("/send-report")
def send_report(body: SendReportIn, session: SessionDep, _admin: AdminUser) -> dict[str, Any]:
    if not body.assessment_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ASSESSMENT_ID_REQUIRED)

    doc = crud.get_assessment(session, str(body.assessment_id))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, ASSESSMENT_NOT_FOUND)
    if not doc.get("email"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ASSESSMENT_NO_EMAIL)

    data = build_report_data(doc, body.answer_summary)
    try:
        subject, html = render_survey_report(data)
        email_service.send(data["email"], subject, html, from_=settings.EMAILS_FROM_BRIEFINGS)
    except Exception as exc:  # noqa: BLE001
        log.error("[admin/send-report] error: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc) or EMAIL_FAILED)

    # Record the send so the admin panel shows it; never fails the request.
    try:
        crud.mark_assessment_sent(session, doc["id"], iso_z(), "admin")
    except Exception:  # noqa: BLE001
        session.rollback()
        log.exception("Failed to record email_sent_at")
    return {"success": True}
