"""Daily briefings: pipeline intake, admin approval/broadcast, n8n feed, public reads.

Ports `frontend/app/api/briefings/{generate,approve,send,delete,today}/route.ts` (same
paths under /api/v1, same status codes and bodies, `{error: ...}` shape kept because n8n
and the admin page read `error`) and adds the read endpoints that replace the server
components' direct `lib/db` queries.
"""

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.api.deps import SessionDep, require_role
from app.crud import editorial as crud
from app.editorial import briefings as logic
from app.editorial import config, emails
from app.editorial.docs import briefing_doc, first_truthy, js_json, parse_json
from app.models.editorial import PUBLISHED_BRIEFING_STATUSES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/briefings", tags=["briefings"])

AdminOnly = Depends(require_role("admin"))


def _err(message: str, status: int, **extra: Any) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


def _secret_ok(provided: str | None, expected: str) -> bool:
    provided = (provided or "").strip()
    return bool(expected) and hmac.compare_digest(provided.encode(), expected.encode())


def _bearer(request: Request) -> str:
    return (request.headers.get("authorization") or "").replace("Bearer ", "").strip()


# ── public reads (server components) ───────────────────────────────────────
class DocsOut(BaseModel):
    docs: list[dict[str, Any]]
    total: int


class AdminDocsOut(BaseModel):
    documents: list[dict[str, Any]]
    total: int


@router.get("", response_model=DocsOut)
def list_published(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocsOut:
    """Published (approved|sent) briefings, newest first. Never returns approval tokens."""
    rows, total = crud.list_briefings(
        session, statuses=PUBLISHED_BRIEFING_STATUSES, limit=limit, offset=offset
    )
    return DocsOut(docs=[briefing_doc(r) for r in rows], total=total)


@router.get("/by-slug/{slug}", response_model=None)
def get_by_slug(slug: str, session: SessionDep) -> dict[str, Any] | JSONResponse:
    row = crud.get_briefing_by_slug(session, slug)
    if row is None:
        return _err("Not found", 404)
    return briefing_doc(row)


@router.get("/admin/all", response_model=AdminDocsOut, dependencies=[AdminOnly])
def list_for_admin(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> AdminDocsOut:
    rows, total = crud.list_briefings(session, statuses=None, limit=limit)
    return AdminDocsOut(documents=[briefing_doc(r) for r in rows], total=total)


# ── generate ────────────────────────────────────────────────────────────────
@router.get("/generate", status_code=410)
def generate_disabled() -> JSONResponse:
    return _err("Vercel cron disabled. Use n8n pipeline → POST /api/briefings/generate.", 410)


@router.post("/generate", response_model=None)
async def generate(
    request: Request, session: SessionDep, forDate: str | None = None
) -> JSONResponse:  # noqa: N803
    provided = request.headers.get("x-cron-secret") or _bearer(request)
    if not _secret_ok(provided, config.cron_secret()):
        return _err("Unauthorized.", 401)

    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    try:
        if not body.get("title"):
            briefing, topic = logic.auto_generate(session, forDate)
            return JSONResponse(
                {
                    "success": True,
                    "briefingId": str(briefing.id),
                    "slug": briefing.slug,
                    "topic": topic,
                }
            )
        briefing = logic.create_manual_briefing(session, body)
        return JSONResponse(
            {"success": True, "briefingId": str(briefing.id), "slug": briefing.slug}
        )
    except Exception:
        log.exception("Briefing generate error")
        return _err("Failed to generate briefing.", 500)


# ── approve (one-click link in the admin email) ────────────────────────────
def _admin_redirect(query: str) -> RedirectResponse:
    return RedirectResponse(f"{config.site_url()}/admin?{query}", status_code=307)


@router.get("/approve", response_class=RedirectResponse)
def approve(
    session: SessionDep,
    token: str | None = None,
    briefingId: str | None = None,  # noqa: N803
) -> RedirectResponse:
    if not token or not briefingId:
        return _admin_redirect("briefing=error&reason=missing-params")
    try:
        briefing = crud.get_briefing(session, briefingId)
        if briefing is None:
            raise LookupError(f"Briefing {briefingId} not found")
        if not hmac.compare_digest((briefing.approval_token or "").encode(), token.encode()):
            return _admin_redirect("briefing=error&reason=invalid-token")
        if briefing.status == "sent":
            return _admin_redirect("briefing=already-sent")

        result = emails.broadcast(session, briefing)
        crud.update_briefing(
            session,
            briefing,
            status="sent",
            sent_at=datetime.now(UTC),
            subscriber_count=result.sent,
        )
        return _admin_redirect(f"briefing=published&sent={result.sent}")
    except Exception:
        log.exception("Briefing approve error")
        return _admin_redirect("briefing=error&reason=server-error")


# ── send (admin dashboard button) ──────────────────────────────────────────
class SendIn(BaseModel):
    briefingId: str | None = None  # noqa: N815 — wire name


@router.post("/send", response_model=None, dependencies=[AdminOnly])
def send(body: SendIn, session: SessionDep) -> JSONResponse:
    if not body.briefingId:
        return _err("briefingId is required.", 400)
    try:
        briefing = crud.get_briefing(session, body.briefingId)
        if briefing is None:
            return _err("Briefing not found", 404)
        if briefing.status != "approved":
            return _err(
                f'Briefing status is "{briefing.status}". Only approved briefings can be sent.', 400
            )
        result = emails.broadcast(session, briefing)
        crud.update_briefing(
            session,
            briefing,
            status="sent",
            sent_at=datetime.now(UTC),
            subscriber_count=result.sent,
        )
        return JSONResponse(
            {"success": True, "sent": result.sent, "failed": result.failed, "total": result.total}
        )
    except Exception:
        log.exception("Briefing send error")
        return _err("Failed to send briefing.", 500)


# ── delete (pipeline duplicate cleanup) ────────────────────────────────────
@router.delete("/delete", response_model=None)
def delete(request: Request, session: SessionDep, id: str | None = None) -> JSONResponse:  # noqa: A002
    try:
        if not _secret_ok(_bearer(request), config.briefing_cron_secret()):
            return _err("Unauthorized", 401)
        if not id:
            return _err("Missing ?id= parameter", 400)
        row = crud.get_briefing(session, id)
        if row is not None:
            crud.delete_briefing(session, row)
        return JSONResponse({"success": True, "deleted": id})
    except Exception as e:
        log.exception("Briefing delete error")
        return _err(str(e), 500)


# ── today (n8n email blast feed) ───────────────────────────────────────────
def _action_items(raw: Any) -> list[str]:
    parsed = parse_json(raw or "[]", [])
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and item.get("action"):
            out.append(str(item["action"]))
        else:
            out.append(js_json(item))
    return out


@router.get("/today", response_model=None)
def today(request: Request, session: SessionDep) -> JSONResponse:
    if not _secret_ok(_bearer(request), config.cron_secret()):
        return _err("Unauthorized.", 401)
    try:
        today_ist, start, end = logic.today_ist_window(datetime.now(UTC))
        b = crud.latest_briefing_between(session, start, end)
        if b is None:
            return _err(
                "No briefing found for today.",
                404,
                date=today_ist,
                hint="Pipeline may not have run yet, or no topic was planned for today.",
            )

        why = parse_json(b.why_it_matters or "", {})
        if not isinstance(why, dict):
            why = {}

        def w(*keys: str) -> Any:
            return first_truthy(*(why.get(k) for k in keys))

        return JSONResponse(
            {
                "briefingId": str(b.id),
                "title": b.title or "",
                "slug": b.slug or "",
                "url": f"{config.site_url()}/briefings/{b.slug}",
                "date": today_ist,
                "hook_line1": w("hook_line1", "why_heading", "why"),
                "hook_line2": w("hook_line2"),
                "card_what": w("card_what"),
                "card_why": w("card_why", "impact"),
                "card_action": w("card_action"),
                "card_owner": w("card_owner"),
                "explainer_concept": w("explainer_concept"),
                "explainer_example": w("explainer_example"),
                "explainer_mistake": w("explainer_mistake"),
                "action_items": _action_items(b.action_checklist),
                "save_line": first_truthy(why.get("save_line"), why.get("save"), b.summary),
                "excerpt": b.excerpt or "",
                "read_time": b.read_time or 3,
                "category": b.category or "",
                "author": b.author or "DPDPA Editorial Team",
                "status": b.status or "approved",
                "created_at": b.created_at_attr.isoformat() if b.created_at_attr else "",
            }
        )
    except Exception:
        log.exception("[briefings/today] error")
        return _err("Failed to fetch today's briefing.", 500)
