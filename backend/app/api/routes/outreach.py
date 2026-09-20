"""Outreach campaign: CSV import, stats and contact list (admin), the magic-link
subscribe/unsubscribe pages (public), and the manual triggers for the two daily send
jobs (`/cron/outreach-send`, `/cron/briefing-send`, Bearer CRON_SECRET).

Ports `app/api/outreach/*` and `app/api/cron/{outreach-send,briefing-send}`.
"""

import csv
import hmac
import io
import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from app.api.deps import SessionDep, require_role
from app.core.config import settings
from app.crud import forms as forms_crud
from app.crud import outreach as crud
from app.jobs import outreach as jobs
from app.models.forms import Subscriber
from app.models.outreach import OutreachContact

log = logging.getLogger(__name__)

router = APIRouter(prefix="/outreach", tags=["outreach"])
cron_router = APIRouter(prefix="/cron", tags=["outreach"])

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# CSV-only on purpose: this parses an uploaded file, and xlsx parsers are exactly the
# wrong thing to feed untrusted input. Excel exports CSV in one click.
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
MAX_ROWS = 10_000
IMPORT_SOURCE = "excel_import_v1"

EMAIL_ALIASES = ("email", "e-mail", "emailaddress", "email address", "mail", "emailid", "email id")
NAME_ALIASES = ("name", "full name", "fullname", "contact name", "contactname", "person")
CO_ALIASES = ("company", "organisation", "organization", "business", "firm", "company name")
IND_ALIASES = ("industry", "sector", "vertical", "domain")

STAT_KEYS = ("pending", "sent", "subscribed", "bounced", "unsubscribed", "complained")


def now_utc() -> datetime:
    return datetime.now(UTC)


# ── CSV helpers ───────────────────────────────────────────────────────────
def parse_csv(text: str) -> list[list[str]]:
    """RFC-4180 rows (quoted fields, escaped quotes, CRLF/LF). Blank lines are dropped
    and parsing stops after MAX_ROWS data rows, as the TypeScript parser did."""
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text, newline="")):
        if not row or row == [""]:
            continue
        rows.append(row)
        if len(rows) > MAX_ROWS:
            break
    return rows


def detect_column(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    for h in headers:
        if " ".join(h.lower().split()) in aliases:
            return h
    return None


def _cell(row: dict[str, str], column: str | None) -> str:
    return (row.get(column) or "").strip() if column else ""


# ── admin: import / stats / contacts ──────────────────────────────────────
class ImportOut(BaseModel):
    success: bool = True
    total: int
    inserted: int
    duplicates: int
    invalid: int


@router.post("/import", dependencies=[Depends(require_role("admin"))])
def import_contacts(session: SessionDep, file: UploadFile | None = File(None)) -> ImportOut:
    if file is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No file uploaded.")
    data = file.file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, "File too large (max 2 MB). Export as CSV."
        )
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only CSV files are accepted. In Excel: File → Save As → CSV.",
        )

    grid = parse_csv(data.decode("utf-8-sig", errors="replace"))
    if len(grid) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Spreadsheet is empty.")

    headers = [h.strip() for h in grid[0]]
    rows = [
        {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)}
        for cells in grid[1:]
    ]

    email_col = detect_column(headers, EMAIL_ALIASES)
    if email_col is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Cannot detect email column. Columns found: {', '.join(headers)}. "
            'Rename your email column to "Email".',
        )
    name_col = detect_column(headers, NAME_ALIASES)
    co_col = detect_column(headers, CO_ALIASES)
    ind_col = detect_column(headers, IND_ALIASES)

    existing = crud.existing_contact_emails(session)
    now = now_utc()
    invalid = dupes = inserted = 0

    for row in rows:
        address = _cell(row, email_col).lower()
        if not EMAIL_RE.match(address):
            invalid += 1
            continue
        if address in existing:
            dupes += 1
            continue
        existing.add(address)

        contact = OutreachContact(
            email=address,
            source=IMPORT_SOURCE,
            status="pending",
            magic_token=jobs.generate_token(),
            created_at_attr=now,
            name=_cell(row, name_col) or None,
            company=_cell(row, co_col) or None,
            industry=_cell(row, ind_col) or None,
        )
        if crud.insert_contact(session, contact):
            inserted += 1
    session.commit()

    return ImportOut(total=len(rows), inserted=inserted, duplicates=dupes, invalid=invalid)


class StatsOut(BaseModel):
    total: int
    pending: int
    sent: int
    subscribed: int
    bounced: int
    unsubscribed: int
    complained: int


@router.get("/stats", dependencies=[Depends(require_role("admin"))])
def stats(session: SessionDep) -> StatsOut:
    counts = crud.count_contacts_by_status(session)
    return StatsOut(total=sum(counts.values()), **{k: counts.get(k, 0) for k in STAT_KEYS})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def contact_doc(c: OutreachContact) -> dict[str, Any]:
    """The document shape `/api/admin/data?collection=outreach_contacts` returned
    (application field `created_at` ← column `created_at_attr`)."""
    return {
        "$id": str(c.id),
        "id": str(c.id),
        "email": c.email,
        "name": c.name,
        "company": c.company,
        "industry": c.industry,
        "source": c.source,
        "status": c.status,
        "intro_sent_at": _iso(c.intro_sent_at),
        "subscribed_at": _iso(c.subscribed_at),
        "created_at": _iso(c.created_at_attr),
    }


@router.get("/contacts", dependencies=[Depends(require_role("admin"))])
def list_contacts(
    session: SessionDep,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = 200,
) -> dict[str, Any]:
    """Replaces this page's use of `/api/admin/data?collection=outreach_contacts`:
    newest first, `limit` capped at 500, same `{documents, total}` shape."""
    docs, total = crud.list_contacts(
        session, status=status_filter or None, limit=max(1, min(limit, 500))
    )
    return {"documents": [contact_doc(c) for c in docs], "total": total}


# ── public: magic-link subscribe / unsubscribe ────────────────────────────
class TokenIn(BaseModel):
    token: Any = None


def _require_token(body: TokenIn) -> str:
    if not body.token or not isinstance(body.token, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid token.")
    return str(body.token)


class SubscribeOut(BaseModel):
    success: bool = True
    already: bool
    name: str


@router.post("/subscribe")
def subscribe(body: TokenIn, session: SessionDep) -> SubscribeOut:
    token = _require_token(body)
    contact = crud.get_contact_by_token(session, token)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised or already used.")

    name = contact.name or ""
    if contact.status == "subscribed":
        return SubscribeOut(already=True, name=name)

    address = contact.email.strip().lower()
    now = now_utc()

    if crud.get_subscriber_by_email(session, address) is None:
        # Explicit one-click consent from the intro email.
        forms_crud.create_subscriber(
            session,
            Subscriber(
                name=name,
                email=address,
                industry=contact.industry or "",
                frequency="daily",
                consent_version=forms_crud.PRIVACY_NOTICE_VERSION,
                consent_source="intro_email_one_click",
                status="active",
                created_at_attr=now,
                ip_address="",
                city="",
                country="",
                region="",
                user_agent="",
            ),
        )
        try:
            forms_crud.log_consent(
                session,
                email=address,
                name=name,
                source="outreach_magic_link",
                consent_type="email_marketing",
                timestamp=now.isoformat(),
            )
        except Exception:  # noqa: BLE001 — audit row is best effort, as before
            session.rollback()
            log.exception("[outreach/subscribe] consent_log write failed")

    contact.status = "subscribed"
    contact.subscribed_at = now
    crud.save(session, contact)
    return SubscribeOut(already=False, name=name)


class UnsubscribeOut(BaseModel):
    success: bool = True
    already: bool


@router.post("/unsubscribe")
def unsubscribe(body: TokenIn, session: SessionDep) -> UnsubscribeOut:
    token = _require_token(body)
    contact = crud.get_contact_by_token(session, token)
    if contact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not recognised.")
    if contact.status == "unsubscribed":
        return UnsubscribeOut(already=True)
    contact.status = "unsubscribed"
    crud.save(session, contact)
    return UnsubscribeOut(already=False)


# ── cron triggers (manual runs; the worker runs the same functions) ───────
def require_cron_secret(request: Request) -> None:
    secret = settings.CRON_SECRET
    supplied = request.headers.get("authorization", "")
    if not secret or not hmac.compare_digest(supplied.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")


def _answer(result: tuple[int, dict[str, Any]]) -> dict[str, Any]:
    code, body = result
    if code != 200:
        raise HTTPException(code, body.get("error"))
    return body


@cron_router.get("/outreach-send", dependencies=[Depends(require_cron_secret)])
def cron_outreach_send(session: SessionDep) -> dict[str, Any]:
    return _answer(jobs.outreach_send(session))


@cron_router.get("/briefing-send", dependencies=[Depends(require_cron_secret)])
def cron_briefing_send(session: SessionDep) -> dict[str, Any]:
    return _answer(jobs.briefing_send(session))
