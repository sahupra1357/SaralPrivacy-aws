"""Human-handoff packet — SETU_BINDU_CHATBOT_SPEC §9.5. Rewrite of
``frontend/lib/chat/handoff.ts``.

Two rules hold this module up:

1. A packet cannot exist without consent. :func:`validate_handoff_input` is the only way
   to obtain a contact, and it refuses anything but a literal ``True``.
2. Free text is redacted BEFORE assembly, never after. Both fields derive from user turns
   and may carry a PAN or phone the user typed despite the PII warning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeGuard

from app.services.chat.journeys import ChatSessionState, journey_by_id
from app.services.chat.orchestrate import EscalationReason
from app.services.chat.redact import redact_text

MAX_FREETEXT = 500
MAX_SOURCES = 10

ESCALATION_REASONS: tuple[str, ...] = (
    "explicit_ask",
    "journey_stalled",
    "repeat_refusal",
    "negative_feedback",
)

# pagesShown is machine-populated with site paths, so anything that is not a site path is
# a caller inventing one. Dropping rather than escaping keeps the lead readable — an admin
# should see the guides Setu showed, not a mangled payload.
SITE_PATH_RE = re.compile(r"^/[A-Za-z0-9\-_/]{0,120}$")

#: Industry slugs are lowercase-hyphen only; anything else is invented.
SITE_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")

# Deliberately permissive: this gates obvious rubbish, not deliverability. Rejecting a real
# address because it has an unusual TLD costs a lead.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def iso_ms(dt: datetime) -> str:
    """`Date.prototype.toISOString()` — UTC, exactly three fractional digits, "Z"."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def is_escalation_reason(v: Any) -> TypeGuard[EscalationReason]:
    return isinstance(v, str) and v in ESCALATION_REASONS


def escape_html(s: str) -> str:
    """The packet's destination is an HTML email whose template interpolates every field
    raw. ``redact`` strips PII patterns; it does not touch markup, so without this anything
    a caller can put into ChatSessionState arrives as live HTML in an internal alert —
    factsConfirmed keys and values, and pagesShown, none of which ``sanitize_state``
    validates beyond "is a string"."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@dataclass(frozen=True)
class HandoffPacket:
    summary: str
    unresolved_question: str
    intent: str
    sources_shown: list[str]
    page_url: str
    reason: str
    ts: str
    journey: str | None = None
    industry: str | None = None
    #: Always True — a packet is never built without consent.
    consent_to_contact: bool = True


def _summarise(state: ChatSessionState, last_user_message: str) -> str:
    """Plain-language description of what the visitor was trying to do, assembled from
    state rather than asked for. The person picking this up should be able to open the
    email and know the situation without replaying a transcript."""
    bits: list[str] = []
    if state.journey:
        bits.append(f"Working through: {journey_by_id(state.journey).name}")
    if state.industry:
        bits.append(f"Industry: {state.industry}")
    if state.facts_confirmed:
        joined = ", ".join(f"{k}={v}" for k, v in state.facts_confirmed.items())
        bits.append(f"Told us: {joined}")
    bits.append(f"Turns: {state.message_count}")
    if last_user_message.strip():
        bits.append(f"Last asked: {last_user_message.strip()}")
    # Redact first (PII), then escape (markup) — both before the string can reach the email
    # template. Order matters only in that escaping first would let &lt;-encoded text hide a
    # pattern from the redactor.
    return escape_html(redact_text(" · ".join(bits)))[:MAX_FREETEXT]


def build_handoff_packet(
    *,
    state: ChatSessionState,
    page_url: str,
    reason: EscalationReason,
    last_user_message: str,
    now: datetime | None = None,
) -> HandoffPacket:
    """Build the packet server-side from already-sanitised state. Deliberately does NOT
    accept a client-supplied packet — same discipline as ChatMeta, where letting the
    untrusted side author the object is the whole class of bug the architecture exists to
    prevent."""
    # sanitize_state slices `industry` without checking membership, so treat it as
    # untrusted here rather than trusting the type.
    industry = state.industry if state.industry and SITE_SLUG_RE.fullmatch(state.industry) else None
    ts = iso_ms(now or datetime.now(UTC))
    return HandoffPacket(
        summary=_summarise(state, last_user_message),
        unresolved_question=escape_html(redact_text(last_user_message))[:MAX_FREETEXT],
        intent=journey_by_id(state.journey).name if state.journey else "general enquiry",
        journey=state.journey,
        industry=industry,
        sources_shown=[p for p in state.pages_shown if SITE_PATH_RE.fullmatch(p)][:MAX_SOURCES],
        page_url=page_url[:200],
        reason=reason,
        consent_to_contact=True,
        ts=ts,
    )


@dataclass(frozen=True)
class HandoffContact:
    name: str
    email: str


@dataclass(frozen=True)
class HandoffValidation:
    ok: bool
    contact: HandoffContact | None = None
    error: str | None = None


def validate_handoff_input(body: dict[str, Any]) -> HandoffValidation:
    """Three fields, and consent must be literal ``True``. Any more fields and the form
    becomes the thing people abandon; company and phone are deliberately not collected for
    what is only a callback request (§6.3, data minimisation)."""
    raw_name = body.get("name")
    raw_email = body.get("email")
    name = raw_name.strip()[:100] if isinstance(raw_name, str) else ""
    email = raw_email.strip()[:200] if isinstance(raw_email, str) else ""

    if body.get("consent") is not True:
        return HandoffValidation(ok=False, error="Consent is required.")
    if not name:
        return HandoffValidation(ok=False, error="Please add your name.")
    if not EMAIL_RE.fullmatch(email):
        return HandoffValidation(ok=False, error="Please check the email address.")

    return HandoffValidation(ok=True, contact=HandoffContact(name=name, email=email))
