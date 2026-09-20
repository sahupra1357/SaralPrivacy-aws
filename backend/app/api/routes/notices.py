"""Notice Pack Builder — lead capture, funnel events and the server-rendered PDF.

Ports `frontend/app/api/notice/{capture,events,pdf}/route.ts`. Public paths move from
`/api/notice/<x>` to `/api/v1/notices/<x>`; status codes, guards and user-facing strings
are unchanged.

Bodies are validated by hand rather than in the signature: the TypeScript used
`zod.safeParse` and answered with its own 400 text (or, for events, silently succeeded),
where a declared FastAPI body would answer 422 with a field report.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.deps import RateLimit, SessionDep, client_geo, client_ip
from app.core.config import settings
from app.core.ratelimit import hit
from app.crud import notices as crud
from app.notice_pack import render
from app.notice_pack.state import NoticeState
from app.services import email as email_service
from app.services import pdf as pdf_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/notices", tags=["notices"])

# abuseGuard.ts: bots fill every field they find; the form keeps this one visually
# hidden, so a real visitor always leaves it empty.
HONEYPOT_FIELD = "hp_url"

INVALID_CAPTURE = "Invalid email or fields."
UNEXPECTED_ERROR = "An unexpected error occurred. Please try again."
INVALID_BODY = "Invalid request body."
INVALID_NOTICE = "Invalid notice data."
PDF_FAILED = "Could not generate the PDF. Please try the print fallback."

EventName = Literal[
    "notice_builder_started",
    "business_type_selected",
    "data_categories_confirmed",
    "purpose_matrix_completed",
    "collection_contexts_selected",
    "notice_preview_generated",
    "notice_score_calculated",
    "notice_lead_captured",
    "notice_pdf_downloaded",
    "notice_html_copied",
    "mini_notice_copied",
    "consent_block_copied",
    "dsar_cta_clicked",
    "notice_evidence_record_created",
]


# ── Request / response models ────────────────────────────────────────────────
class NoticeCaptureIn(BaseModel):
    email: EmailStr
    name: Annotated[str, Field(max_length=120)] = ""
    business_name: Annotated[str, Field(max_length=160)] = ""
    sector: Annotated[str, Field(max_length=80)] = ""
    readiness_score: Annotated[int, Field(ge=0, le=100)] | None = None
    export_type: Literal["pdf", "copy", "pack"] | None = None
    source: Annotated[str, Field(max_length=60)] = "notice-generator"
    consent: bool = False


class NoticeCaptureOut(BaseModel):
    success: bool


class NoticeEventIn(BaseModel):
    """Only non-PII fields are accepted into the payload."""

    name: EventName
    session_id: Annotated[str, Field(max_length=64)] = ""
    sector: Annotated[str, Field(max_length=80)] | None = None
    score: Annotated[int, Field(ge=0, le=100)] | None = None
    context: Annotated[str, Field(max_length=60)] | None = None


class NoticeEventOut(BaseModel):
    ok: bool


class NoticePdfIn(NoticeState):
    effIso: datetime | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────
def _iso_now() -> str:
    """`new Date().toISOString()` — millisecond precision, `Z` suffix."""
    now = datetime.now(UTC)
    return f"{now:%Y-%m-%dT%H:%M:%S}.{now.microsecond // 1000:03d}Z"


def _honeypot_tripped(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    value = body.get(HONEYPOT_FIELD)
    return isinstance(value, str) and bool(value.strip())


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:  # noqa: BLE001 — any malformed body is one 400, as in the TS
        return None


def send_notice_lead_alert(
    *,
    email: str,
    business_name: str,
    sector: str,
    readiness_score: int | None,
    export_type: str,
) -> None:
    """Founder alert for a Notice Pack export lead. Never raises: the capture must
    succeed even when mail does (the TypeScript logged and swallowed the same way)."""
    try:
        recipient = settings.ADMIN_EMAIL
        if not recipient:
            log.warning("ADMIN_EMAIL unset — notice lead alert not sent")
            return
        sector_suffix = f" ({sector})" if sector else ""
        subject = f"Notice Pack lead — {business_name or email}{sector_suffix}"
        html = email_service.render(
            "notice_lead_alert.html",
            email=email,
            business_name=business_name,
            sector=sector,
            readiness_score=f"{readiness_score} / 100" if readiness_score is not None else "",
            export_type=export_type,
        )
        email_service.send(recipient, subject, html, from_=settings.EMAILS_FROM_NOREPLY)
    except Exception as exc:  # noqa: BLE001
        log.error("sendNoticeLeadAlert error: %s", exc)


# ── Routes ───────────────────────────────────────────────────────────────────
@router.post(
    "/capture",
    response_model=NoticeCaptureOut,
    dependencies=[Depends(RateLimit("notice-capture", 8, 60))],
)
async def capture_notice_lead(
    request: Request,
    session: SessionDep,
    background: BackgroundTasks,
) -> NoticeCaptureOut:
    """Export-gate lead capture: store the lead, then alert the founder out of band."""
    raw = await _read_json(request)

    # Honeypot: bots fill the hidden field. Pretend success, store nothing.
    if _honeypot_tripped(raw):
        return NoticeCaptureOut(success=True)

    try:
        body = NoticeCaptureIn.model_validate(raw)
    except ValidationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_CAPTURE) from None

    name = body.name or body.email.split("@")[0]
    export_type = body.export_type or ""
    try:
        await run_in_threadpool(
            crud.create_capture,
            session,
            email=body.email,
            name=name,
            business_name=body.business_name,
            sector=body.sector,
            readiness_score=body.readiness_score if body.readiness_score is not None else 0,
            export_type=export_type,
            source=body.source,
            consent=body.consent,
            ip_address=client_ip(request),
            city=client_geo(request).city,
            country=client_geo(request).country,
            created_at_attr=_iso_now(),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("notice capture error: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, UNEXPECTED_ERROR) from None

    # Fire-and-forget founder alert — never block the response.
    background.add_task(
        send_notice_lead_alert,
        email=body.email,
        business_name=body.business_name,
        sector=body.sector,
        readiness_score=body.readiness_score,
        export_type=export_type,
    )
    return NoticeCaptureOut(success=True)


@router.post("/events", response_model=NoticeEventOut)
async def record_notice_event(request: Request, session: SessionDep) -> NoticeEventOut:
    """Owned funnel sink. Analytics must never break UX, so every failure answers 200."""
    ok = NoticeEventOut(ok=True)
    limit = await run_in_threadpool(hit, session, f"notice-events:{client_ip(request)}", 60, 60)
    if not limit.ok:
        return ok  # silently drop floods

    raw = await _read_json(request)
    try:
        body = NoticeEventIn.model_validate(raw)
    except ValidationError:
        return ok  # drop unknown events, don't error the client

    payload = body.model_dump(exclude_unset=True, exclude={"name", "session_id"})
    try:
        await run_in_threadpool(
            crud.create_event,
            session,
            name=body.name,
            session_id=body.session_id,
            payload=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            created_at_attr=_iso_now(),
        )
    except Exception as exc:  # noqa: BLE001
        log.error("notice events error: %s", exc)
    return ok


@router.post(
    "/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "The notice as a PDF"}},
    dependencies=[Depends(RateLimit("notice-pdf", 6, 60))],
)
async def notice_pdf(request: Request) -> Response:
    """Render the user's own answers to a branded A4 PDF.

    Permissive schema: we only PDF the user's own answers, and the renderer escapes every
    field, so the risk is bounded. Shape and sizes are validated to keep the render fast
    and the payload sane. CPU-bound — the client keeps a browser-print fallback.
    """
    raw = await _read_json(request)
    if raw is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_BODY)
    try:
        body = NoticePdfIn.model_validate(raw)
    except ValidationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_NOTICE) from None

    try:
        html = render.notice_document_html(body, body.effIso)
        content = await run_in_threadpool(
            pdf_service.render_html,
            html,
            header_html=render.PDF_HEADER,
            footer_html=render.PDF_FOOTER,
            format="A4",
            margins=render.PDF_MARGINS,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("notice pdf error: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, PDF_FAILED) from None

    filename = f"dpdpa-privacy-notice-{render.slug_for_file(body.org)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


__all__ = ["router", "send_notice_lead_alert"]
