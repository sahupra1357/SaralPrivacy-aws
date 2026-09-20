"""Outreach jobs and the bulk-email policy they share.

Two daily jobs (registered by the orchestrator in app/jobs/__init__.py):

    outreach-send   "0 4 * * *"   one-time intro email (today's briefing) to cold contacts
    briefing-send   "30 4 * * *"  the latest approved briefing to every eligible subscriber

Both also have a manual HTTP trigger (`GET /api/v1/cron/<name>` with
`Authorization: Bearer CRON_SECRET`) that returns the same JSON the Vercel crons did.

This file is also the single home of what `lib/sendGateway.ts` and the briefing half
of `lib/email.ts` did, so the editorial module's approve/send routes can import
`eligible_subscribers`, `build_unsubscribe_url` and `send_briefing_to_subscribers`
from here instead of porting them a second time.
"""

import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from sqlmodel import Session

from app.core.config import settings
from app.crud import outreach as crud
from app.jobs.registry import JobResult
from app.services import email

log = logging.getLogger(__name__)

OUTREACH_FROM = "SaralPrivacy <briefings@news.saralprivacy.com>"
OUTREACH_REPLY_TO = "privacy@saralprivacy.com"
NO_BRIEFING = "No approved briefing found. Approve a briefing first."
NO_PENDING = "No pending contacts. Campaign complete."
NO_ELIGIBLE = "No eligible subscribers today."
SEND_PAUSE_SECONDS = 0.1  # "Avoid Resend rate limits" — 100 ms between briefing sends

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


# ── small helpers ─────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(UTC)


def site_url() -> str:
    return (settings.NEXT_PUBLIC_SITE_URL or "https://saralprivacy.com").rstrip("/")


def generate_token() -> str:
    """lib/tokens.ts: 32 random bytes, base64url without padding."""
    return secrets.token_urlsafe(32)


def format_long_date(moment: datetime) -> str:
    """`toLocaleDateString('en-IN', {weekday, year, month: 'long', day})` on a UTC
    server, e.g. "Friday, 18 September 2026"."""
    d = moment.astimezone(UTC)
    return f"{_WEEKDAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


def _parse_when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def parse_checklist(raw: Any) -> list[str]:
    """`JSON.parse(action_checklist || '[]')`; anything unparsable or not a list → []."""
    try:
        items = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(i) for i in items] if isinstance(items, list) else []


def why_text(raw: Any) -> str:
    """Outreach email: why_it_matters may be a JSON object carrying `hook_line1` or
    `why`; otherwise the raw text is used."""
    raw_text = str(raw or "")
    try:
        parsed = json.loads(raw_text)
    except ValueError:
        return raw_text
    if isinstance(parsed, dict):
        return str(parsed.get("hook_line1") or parsed.get("why") or raw_text)
    return raw_text


# ── sendGateway ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EligibleSubscriber:
    id: str
    email: str
    name: str
    frequency: str


def eligible_subscribers(session: Session) -> list[EligibleSubscriber]:
    """Every subscriber not unsubscribed/bounced/complained, deduped by lowercased email
    (first row wins), frequency defaulting to daily."""
    seen: set[str] = set()
    out: list[EligibleSubscriber] = []
    for sub in crud.eligible_subscribers(session):
        addr = (sub.email or "").strip().lower()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        out.append(
            EligibleSubscriber(
                id=str(sub.id), email=addr, name=sub.name or "", frequency=sub.frequency or "daily"
            )
        )
    return out


def sign_email_for_unsubscribe(address: str) -> str | None:
    secret = (settings.EMAIL_LINK_SECRET or "").strip()
    if not secret:
        return None
    return hmac.new(secret.encode(), address.strip().lower().encode(), hashlib.sha256).hexdigest()


def build_unsubscribe_url(address: str) -> str:
    """`${SITE}/unsubscribe?email=<encoded>[&sig=<hex>]` — verified by the forms module's
    `verify_unsubscribe_sig`. Unsigned when EMAIL_LINK_SECRET is unset."""
    sig = sign_email_for_unsubscribe(address)
    sig_part = f"&sig={sig}" if sig else ""
    encoded = quote(address, safe="!'()*")  # encodeURIComponent
    return f"{site_url()}/unsubscribe?email={encoded}{sig_part}"


# ── rendering ─────────────────────────────────────────────────────────────
def render_briefing_email(briefing: dict[str, Any], unsubscribe_url: str) -> tuple[str, str]:
    """briefingEmailTemplate → (subject, html)."""
    title = str(briefing.get("title") or "")
    when = _parse_when(briefing.get("scheduled_for")) or now_utc()
    html = email.render(
        "outreach/briefing.html",
        title=title,
        summary=briefing.get("summary") or "",
        why_it_matters=briefing.get("why_it_matters") or "",
        content=briefing.get("content") or "",
        checklist=parse_checklist(briefing.get("action_checklist")),
        date_str=format_long_date(when),
        unsubscribe_url=unsubscribe_url,
    )
    return f"DPDPA Daily Brief: {title}", html


def render_outreach_email(
    briefing: dict[str, Any],
    *,
    name: str | None,
    subscribe_url: str,
    unsubscribe_url: str,
) -> tuple[str, str, str]:
    """outreachBriefingEmail → (subject, html, text)."""
    title = str(briefing.get("title") or "")
    checklist = parse_checklist(briefing.get("action_checklist"))
    context: dict[str, Any] = {
        "title": title,
        "summary": briefing.get("summary") or "",
        "why_text": why_text(briefing.get("why_it_matters")),
        "content": briefing.get("content") or "",
        "checklist": checklist,
        "checklist_text": "\n".join(f"{i}. {item}" for i, item in enumerate(checklist, 1)),
        "first_name": (name or "").split(" ")[0] or "there",
        "date_str": format_long_date(now_utc()),
        "subscribe_url": subscribe_url,
        "unsubscribe_url": unsubscribe_url,
    }
    html = email.render("outreach/outreach_briefing.html", **context)
    text = email.render("outreach/outreach_briefing.txt", **context)
    return title, html, text


# ── sending ───────────────────────────────────────────────────────────────
def send_subscriber_briefing(briefing: dict[str, Any], address: str) -> email.SentEmail:
    """sendSubscriberBriefing: one briefing to one subscriber, with List-Unsubscribe."""
    unsubscribe_url = build_unsubscribe_url(address)
    subject, html = render_briefing_email(briefing, unsubscribe_url)
    return email.send(
        address,
        subject,
        html,
        from_=settings.EMAILS_FROM_BRIEFINGS,
        headers={"List-Unsubscribe": f"<{unsubscribe_url}>"},
    )


def send_briefing_to_subscribers(
    briefing: dict[str, Any], subscribers: list[EligibleSubscriber]
) -> tuple[int, int]:
    """sendBriefingToSubscribers (used by editorial's approve/send): returns (sent, failed)."""
    sent = failed = 0
    for sub in subscribers:
        try:
            send_subscriber_briefing(briefing, sub.email)
            sent += 1
        except Exception:  # noqa: BLE001 — one bad address never stops the run
            log.exception("[briefing] send failed for subscriber %s", sub.id)
            failed += 1
        time.sleep(SEND_PAUSE_SECONDS)
    return sent, failed


# ── outreach-send ─────────────────────────────────────────────────────────
def outreach_send(session: Session) -> tuple[int, dict[str, Any]]:
    """Returns (http_status, body) exactly as /api/cron/outreach-send answered."""
    found = crud.briefing_for_outreach(session)
    if found is None:
        return 404, {"error": NO_BRIEFING}
    briefing, is_new = found

    total_pending = crud.count_pending(session)
    contacts = crud.pending_contacts(session, settings.OUTREACH_DAILY_CAP)
    if not contacts:
        return 200, {"message": NO_PENDING, "briefing_used": briefing.get("title")}

    now = now_utc()
    base = site_url()
    sent = failed = 0

    for contact in contacts:
        if not contact.magic_token:
            contact.magic_token = generate_token()
            crud.save(session, contact)
        token = contact.magic_token

        subject, html, text = render_outreach_email(
            briefing,
            name=contact.name,
            subscribe_url=f"{base}/subscribe?token={token}",
            unsubscribe_url=f"{base}/unsubscribe/outreach?token={token}",
        )
        try:
            result = email.send(
                contact.email,
                subject,
                html,
                from_=OUTREACH_FROM,
                reply_to=OUTREACH_REPLY_TO,
                text=text,
            )
        except Exception:  # noqa: BLE001 — mark the contact failed and carry on
            log.exception("[outreach-send] send failed for contact %s", contact.id)
            failed += 1
            try:
                contact.status = "failed"
                crud.save(session, contact)
            except Exception:  # noqa: BLE001 — best effort, as in the TypeScript
                session.rollback()
            continue

        contact.status = "sent"
        contact.intro_sent_at = now
        crud.save(session, contact)
        crud.log_send(
            session,
            recipient_email=contact.email,
            email_type="intro",
            message_id=result.message_id,
            status="sent",
            consent_basis="one_time_dpdpa_sensitization",
            sent_at=now,
        )
        sent += 1

    if is_new and sent > 0:
        try:
            crud.mark_briefing_outreach_used(session, str(briefing["id"]), now)
        except Exception:  # noqa: BLE001 — logged, not fatal (was a detached .catch)
            session.rollback()
            log.exception("[outreach-send] Failed to mark briefing")

    return 200, {
        "sent": sent,
        "failed": failed,
        "remaining": total_pending - sent,
        "briefing_used": briefing.get("title"),
        "briefing_id": str(briefing.get("id")),
    }


def run_outreach_send(session: Session) -> JobResult:
    status, body = outreach_send(session)
    if status != 200:
        return JobResult(ok=False, summary=str(body.get("error")), details=body)
    return JobResult(
        ok=True, summary=str(body.get("message") or f"sent {body.get('sent')}"), details=body
    )


# ── briefing-send ─────────────────────────────────────────────────────────
def briefing_send(session: Session) -> tuple[int, dict[str, Any]]:
    """Returns (http_status, body) exactly as /api/cron/briefing-send answered."""
    briefing = crud.latest_approved_briefing(session)
    if briefing is None:
        return 404, {"error": NO_BRIEFING}

    now = now_utc()
    is_monday = now.weekday() == 0
    eligible = [s for s in eligible_subscribers(session) if s.frequency != "weekly" or is_monday]
    if not eligible:
        return 200, {"message": NO_ELIGIBLE, "briefing_used": briefing.get("title")}

    sent = failed = 0
    for sub in eligible:
        try:
            result = send_subscriber_briefing(briefing, sub.email)
        except Exception:  # noqa: BLE001 — count and continue
            log.exception("[briefing-send] send failed for subscriber %s", sub.id)
            failed += 1
            continue

        try:
            crud.log_send(
                session,
                recipient_email=sub.email,
                email_type="briefing_weekly" if sub.frequency == "weekly" else "briefing_daily",
                message_id=result.message_id,
                status="sent",
                consent_basis="explicit_consent",
                sent_at=now,
            )
        except Exception:  # noqa: BLE001 — the log row is best effort, as before
            session.rollback()
            log.exception("[briefing-send] log write failed")

        sent += 1
        time.sleep(SEND_PAUSE_SECONDS)

    crud.mark_briefing_sent(session, str(briefing["id"]), now, sent)
    log.info("[briefing-send] Done — sent: %s, failed: %s", sent, failed)

    return 200, {
        "sent": sent,
        "failed": failed,
        "total_eligible": len(eligible),
        "briefing_used": briefing.get("title"),
        "briefing_id": str(briefing.get("id")),
    }


def run_briefing_send(session: Session) -> JobResult:
    status, body = briefing_send(session)
    if status != 200:
        return JobResult(ok=False, summary=str(body.get("error")), details=body)
    return JobResult(
        ok=True, summary=str(body.get("message") or f"sent {body.get('sent')}"), details=body
    )
