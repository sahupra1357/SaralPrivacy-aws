"""Resend delivery webhooks (`POST /api/v1/webhooks/resend`).

Port of `app/api/webhooks/resend/route.ts`. The svix signature is verified here in pure
Python (no `svix` package): HMAC-SHA256 over "<svix-id>.<svix-timestamp>.<raw body>"
with the base64 secret that follows "whsec_" in RESEND_WEBHOOK_SECRET, compared with
every "v1,<base64>" entry in the svix-signature header, timestamp within 5 minutes.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session

from app.api.deps import SessionDep
from app.core.config import settings
from app.crud import outreach as crud

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

TOLERANCE_SECONDS = 5 * 60  # the svix library's default

# Resend event → ops.email_send_log.status (the column's check constraint allows
# only these six values). Other events (e.g. email.delivery_delayed) change nothing.
EVENT_STATUS = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.opened": "opened",
    "email.clicked": "clicked",
    "email.bounced": "bounced",
    "email.complained": "complained",
}
# Events that suppress the address everywhere.
SUPPRESSING_EVENTS = {"email.bounced": "bounced", "email.complained": "complained"}


def _secret_bytes(secret: str) -> bytes:
    raw = secret.removeprefix("whsec_")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return raw.encode()


def verify_svix(
    secret: str,
    body: bytes,
    msg_id: str,
    timestamp: str,
    signature_header: str,
    *,
    now: float | None = None,
) -> bool:
    if not (msg_id and timestamp and signature_header):
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > TOLERANCE_SECONDS:
        return False

    signed = msg_id.encode() + b"." + timestamp.encode() + b"." + body
    expected = base64.b64encode(
        hmac.new(_secret_bytes(secret), signed, hashlib.sha256).digest()
    ).decode()

    for part in signature_header.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig.encode(), expected.encode()):
            return True
    return False


def _recipient(payload: dict[str, Any]) -> str:
    data = payload.get("data") or {}
    to = data.get("to")
    first = to[0] if isinstance(to, list) and to else None
    return str(first or data.get("email") or "").strip().lower()


def _record_event(session: Session, email: str, event: str, message_id: str) -> None:
    """Update the send-log rows this message produced; when none match (an event for a
    mail this app did not log, or a provider id that differs from the SMTP Message-ID),
    add one row as the TypeScript did for every event."""
    new_status = EVENT_STATUS.get(event)
    if new_status is None:
        return
    now = datetime.now(UTC)
    try:
        updated = (
            crud.update_send_log_status(session, message_id, new_status, now) if message_id else 0
        )
        if updated == 0:
            crud.log_send(
                session,
                recipient_email=email,
                email_type="intro",
                message_id=message_id,
                status=new_status,
                consent_basis="webhook_event",
                sent_at=now,
            )
    except Exception:  # noqa: BLE001 — the event log is best effort, as before
        session.rollback()
        log.exception("[resend-webhook] email_send_log write failed")


async def raw_body(request: Request) -> bytes:
    """The exact bytes that were signed (read async so the route itself can be sync)."""
    return await request.body()


@router.post("/resend")
def resend_webhook(
    request: Request, session: SessionDep, body: Annotated[bytes, Depends(raw_body)]
) -> dict[str, Any]:
    secret = settings.RESEND_WEBHOOK_SECRET
    if not secret:
        log.error("[resend-webhook] RESEND_WEBHOOK_SECRET not set")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Webhook not configured")

    ok = verify_svix(
        secret,
        body,
        request.headers.get("svix-id", ""),
        request.headers.get("svix-timestamp", ""),
        request.headers.get("svix-signature", ""),
    )
    if not ok:
        log.warning("[resend-webhook] Signature verification failed")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    event = str(payload.get("type") or "")
    email = _recipient(payload)
    if not email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No email in payload")

    message_id = str((payload.get("data") or {}).get("email_id") or "")
    _record_event(session, email, event, message_id)

    suppressed = SUPPRESSING_EVENTS.get(event)
    if suppressed:
        for update in (crud.set_contact_status_by_email, crud.set_subscriber_status_by_email):
            try:
                update(session, email, suppressed)
            except Exception:  # noqa: BLE001 — one table failing never blocks the other
                session.rollback()
                log.exception("[resend-webhook] status update failed")
        log.info("[resend-webhook] Marked %s", suppressed)
    elif event == "email.delivery_delayed":
        log.info("[resend-webhook] Delivery delayed")

    return {"received": True, "event": event, "email": email}
