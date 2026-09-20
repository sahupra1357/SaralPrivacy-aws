"""Setu chat endpoints — two-phase streaming (spec §5.5).

Phase A: Setu's answer streams as plain text.
Phase B: one META_SENTINEL (U+001E) + a server-built ChatMeta JSON block.

The model streams TEXT ONLY. Retrieval runs BEFORE the model call (`plan_turn_hybrid` =
the spec §5.2 search/route/suggest tools executed deterministically each turn); citations,
actions, confidence and refusal are computed server-side in
`app.services.chat.orchestrate`, so an invalid URL cannot reach the client. Below the
retrieval floor the model is never called at all — the canned refusal streams instead
(zero cost, zero risk).

The byte stream is the binding contract with `frontend/components/chat/useSetuChat.ts`:
answer text, then U+001E, then one JSON object. Nothing else may go on the wire.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import SessionDep, client_geo, client_ip
from app.core.ratelimit import hit
from app.crud import chat as crud
from app.models.chat import FAILURE_KINDS
from app.services import email, llm, retrieval
from app.services.chat.briefings_live import (
    FRESH_INTENT_RE,
    briefings_context_block,
    fetch_live_briefings,
)
from app.services.chat.guard import (
    LEAK_HOLDBACK,
    detect_injection,
    history_signing_available,
    new_nonce,
    sanitize_untrusted,
    scan_output,
    sign_turn,
    verify_turn,
    wrap_user_message,
)
from app.services.chat.handoff import (
    build_handoff_packet,
    escape_html,
    is_escalation_reason,
    iso_ms,
    validate_handoff_input,
)
from app.services.chat.journeys import sanitize_state
from app.services.chat.orchestrate import (
    ChatAction,
    ChatCitation,
    ChatMeta,
    EscalationReason,
    build_grounding_block,
    build_meta,
    guarded_meta,
    plan_turn_hybrid,
)
from app.services.chat.protocol import META_SENTINEL
from app.services.chat.redact import redact_text
from app.services.chat.strings import t
from app.services.chat.system_prompt import build_system_prompt, build_turn_notes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 8

#: The route pinned this model explicitly; ANTHROPIC_MODEL is the default for every other
#: caller. The claude-5 family rejects `temperature`, which is why the TypeScript omitted
#: it — see docs/build/status/chat.md, "Needs from orchestrator".
CHAT_MODEL = "claude-sonnet-5"
CHAT_MAX_OUTPUT_TOKENS = 600

#: Copied from frontend/lib/utils.ts. Stamped on every consent row this route writes.
PRIVACY_NOTICE_VERSION = "1.0.0"

#: Shared with frontend/lib/abuseGuard.ts — the two must not drift apart again.
HONEYPOT_FIELD = "hp_url"

TEXT_PLAIN = "text/plain; charset=utf-8"


def _json_body(raw: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or b"")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else {}


def _error(message: str, status: int, headers: dict[str, str] | None = None) -> JSONResponse:
    """The TypeScript answered `{ error }`, not FastAPI's `{ detail }`; the widget and the
    red-team script both read that shape."""
    return JSONResponse({"error": message}, status_code=status, headers=headers)


def _dump(meta: ChatMeta, sig: str | None = None) -> str:
    return json.dumps(meta.to_dict(sig), ensure_ascii=False, separators=(",", ":"))


def _str_field(payload: dict[str, Any], key: str, limit: int) -> str | None:
    v = payload.get(key)
    return v[:limit] if isinstance(v, str) else None


# ── History ───────────────────────────────────────────────────────────────────
def sanitize_history(raw: Any) -> list[dict[str, str]]:
    """History arrives from the browser, so every turn in it is a claim, not a record.
    Two defences apply:

    * all content is tag-neutralised, so no turn can smuggle framing markup;
    * assistant turns must carry a valid HMAC we issued (``guard.sign_turn``), or they are
      dropped. Without that check an attacker can put words in Setu's mouth — "developer
      mode enabled" — and the model reads them as its own prior commitment, which is far
      more persuasive than any user instruction. When CHAT_HISTORY_SECRET is unset we
      cannot verify, so assistant turns are dropped rather than trusted; the user side of
      the conversation still carries continuity.
    """
    if not isinstance(raw, list):
        return []
    can_verify = history_signing_available()
    out: list[dict[str, str]] = []
    for m in raw[-MAX_HISTORY_TURNS:]:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        role = m.get("role")
        if not isinstance(content, str) or role not in ("user", "assistant"):
            continue
        clean = sanitize_untrusted(content[:MAX_MESSAGE_CHARS])
        if role == "assistant":
            if not can_verify or not verify_turn(content.strip(), m.get("sig")):
                continue
        out.append({"role": role, "content": clean})
    return out


def _stream_canned(text_: str, meta: ChatMeta) -> StreamingResponse:
    body = text_ + META_SENTINEL + _dump(meta, sign_turn(text_.strip()))
    return StreamingResponse(iter([body.encode("utf-8")]), media_type=TEXT_PLAIN)


# ── Turn preparation (blocking: retrieval, Pinecone, DB) ──────────────────────
@dataclasses.dataclass
class PreparedTurn:
    refuse: bool
    meta: ChatMeta
    system: str
    turn_notes: str
    grounding: str


def _prepare_turn(
    session: Session, message: str, raw_state: Any, session_id: str, page_url: str | None
) -> PreparedTurn:
    state = sanitize_state(raw_state, session_id, page_url or "")
    # Pinecone semantic search + rerank (D7); falls back to the local lexical index
    # automatically if the vector store is unavailable.
    plan = plan_turn_hybrid(message, state, page_url)
    meta = build_meta(plan)

    if plan.refuse:
        return PreparedTurn(refuse=True, meta=meta, system="", turn_notes="", grounding="")

    turn_notes = build_turn_notes(
        pii_warning=plan.pii_warning,
        refusal_forced=False,
        facts_confirmed=state.facts_confirmed,
        page_url=page_url,
    )
    grounding = build_grounding_block(plan)

    # Freshness intent: fetch the live briefings as a dated context block (Plane 3).
    # Failure degrades silently to the static index.
    if FRESH_INTENT_RE.search(message.lower()):
        live = fetch_live_briefings(session, message)
        if live:
            # Briefings are generated from external news sources, so unlike the rest of the
            # corpus this text is not ours. Neutralise it before it enters a block the
            # prompt treats as trusted — indirect injection travels the same path as direct
            # injection once it is inside the context.
            grounding += "\n\n" + sanitize_untrusted(briefings_context_block(live))
            if not any(c.url == "/briefings" for c in meta.citations):
                meta.citations = [
                    ChatCitation(title="DPDPA Daily Briefings", url="/briefings", tier=4),
                    *meta.citations,
                ][:3]
            if not any(a.url == "/briefings" for a in meta.actions):
                meta.actions = [
                    ChatAction(label="Read the Daily Briefings", url="/briefings"),
                    *meta.actions,
                ][:3]

    return PreparedTurn(
        refuse=False,
        meta=meta,
        system=build_system_prompt(),
        turn_notes=turn_notes,
        grounding=grounding,
    )


def _answer_stream(system: str, messages: list[dict[str, Any]], meta: ChatMeta) -> Iterator[bytes]:
    """Release the stream LEAK_HOLDBACK characters behind the model, so a leak signature is
    always still in the buffer when it completes and never reaches the browser. The cost is
    a fixed ~25-character lag."""
    produced = ""
    pending = ""
    released = ""
    leaked = False
    try:
        for chunk in llm.stream(
            system, messages, model=CHAT_MODEL, max_tokens=CHAT_MAX_OUTPUT_TOKENS
        ):
            produced += chunk
            pending += chunk
            if scan_output(produced).leaked:
                leaked = True
                break
            if len(pending) > LEAK_HOLDBACK:
                flush = pending[: len(pending) - LEAK_HOLDBACK]
                pending = pending[len(pending) - LEAK_HOLDBACK :]
                released += flush
                yield flush.encode("utf-8")

        if leaked:
            # Whatever is still held back contains the signature; drop it and close with
            # the guarded line instead of the model's answer.
            log.warning("[chat-guard] output leak scan tripped — answer withheld")
            tail = f" {t('en', 'guarded')}"
            yield tail.encode("utf-8")
            answer = (released + tail).strip()
            yield (META_SENTINEL + _dump(guarded_meta(), sign_turn(answer))).encode("utf-8")
            return

        yield pending.encode("utf-8")
        answer = (released + pending).strip()
        yield (META_SENTINEL + _dump(meta, sign_turn(answer))).encode("utf-8")
    except Exception:  # noqa: BLE001 — any model/transport failure ends the turn politely
        log.exception("chat stream failed")
        err_meta = dataclasses.replace(meta, confidence="low", animation_state="unsure")
        yield (f"\n{t('en', 'apiError')}" + META_SENTINEL + _dump(err_meta)).encode("utf-8")


# ── POST /api/v1/chat ─────────────────────────────────────────────────────────
@router.post("", response_class=StreamingResponse)
async def chat(request: Request, session: SessionDep) -> Response:
    payload = _json_body(await request.body())
    if payload is None:
        return _error("Invalid JSON body.", 400)

    session_id = _str_field(payload, "sessionId", 64) or ""
    raw_message = payload.get("message")
    message = raw_message.strip() if isinstance(raw_message, str) else ""
    page_url = _str_field(payload, "pageUrl", 200)

    if not session_id or not message:
        return _error("sessionId and message are required.", 400)
    if len(message) > MAX_MESSAGE_CHARS:
        return _error("Message too long (max 2000 characters).", 413)

    # Spec §9.3 MVP limits: burst 5/10s, 30/hr per session, 60/hr per IP.
    ip = client_ip(request)
    for key, limit, window in (
        (f"chat-burst:{session_id}", 5, 10),
        (f"chat-hour:{session_id}", 30, 3600),
        (f"chat-ip:{ip}", 60, 3600),
    ):
        rl = hit(session, key, limit, window)
        if not rl.ok:
            return _error(t("en", "rateLimited"), 429, headers={"Retry-After": str(rl.retry_after)})

    # Instruction-override attempts stop here: before retrieval, before the model, at zero
    # cost. This runs ahead of the plan deliberately — the refusal floor inside the plan is
    # rescued by any router trigger or glossary hit, so appending "what is dpdpa" to a
    # payload would otherwise walk it straight through to the model.
    injection = detect_injection(message)
    if injection.blocked:
        log.warning("[chat-guard] blocked turn (%s)", injection.rule)
        return _stream_canned(t("en", "guarded"), guarded_meta())

    history = sanitize_history(payload.get("history"))
    prepared = await run_in_threadpool(
        _prepare_turn, session, message, payload.get("state"), session_id, page_url
    )

    # Below the floor: refuse without ever calling the model (spec §4.2).
    if prepared.refuse:
        return _stream_canned(f"{t('en', 'refusal')} {t('en', 'refusalHint')}", prepared.meta)

    # The closing delimiter carries an unguessable per-turn nonce, so even a message that
    # somehow evaded neutralisation cannot close the block early and continue in the
    # framework's own voice.
    nonce = new_nonce()
    messages: list[dict[str, Any]] = [
        *history,
        {
            "role": "user",
            "content": (
                f"{prepared.turn_notes}\n\n{prepared.grounding}\n\n"
                f"{wrap_user_message(message, nonce)}"
            ),
        },
    ]
    return StreamingResponse(
        _answer_stream(prepared.system, messages, prepared.meta), media_type=TEXT_PLAIN
    )


# ── POST /api/v1/chat/feedback ────────────────────────────────────────────────
@router.post("/feedback")
async def feedback(request: Request, session: SessionDep) -> Response:
    """👍/👎 + failure-turn logging (decision D2 — redacted, failures only). Question text
    is stored ONLY when `failureKind` is set, and only after `redact_text()`. Successful
    turns store the boolean signal alone."""
    rl = hit(session, f"chat-fb:{client_ip(request)}", 20, 60)
    if not rl.ok:
        return _error("Too many requests.", 429)

    body = _json_body(await request.body())
    if body is None:
        return _error("Invalid JSON body.", 400)

    session_id = _str_field(body, "sessionId", 64) or ""
    turn_id = _str_field(body, "turnId", 64) or ""
    if not session_id or not turn_id:
        return _error("sessionId and turnId are required.", 400)

    raw_kind = body.get("failureKind")
    failure_kind = raw_kind if raw_kind in FAILURE_KINDS else None
    question = body.get("question")

    try:
        crud.create_feedback(
            session,
            session_id=session_id,
            turn_id=turn_id,
            helpful=body["helpful"] if isinstance(body.get("helpful"), bool) else None,
            reason=_str_field(body, "reason", 500),
            page_url=_str_field(body, "pageUrl", 200),
            failure_kind=failure_kind,
            # D2: question text only on failure turns, always redacted.
            redacted_question=(
                redact_text(question[:2000]) if failure_kind and isinstance(question, str) else None
            ),
            ts=datetime.now(UTC),
        )
    except SQLAlchemyError as err:
        # A missing table or a transient database failure must never break chat UX.
        session.rollback()
        log.error("chat_feedback store failed: %s", err)
        return JSONResponse({"stored": False})
    return JSONResponse({"stored": True})


# ── POST /api/v1/chat/handoff ─────────────────────────────────────────────────
def _consultation_alert_html(lead: dict[str, Any]) -> str:
    """The admin alert for a consented callback request.

    Same subject, same rows and the same closing line as
    `frontend/lib/email-templates.ts consultationAlertTemplate`. Every value is escaped
    here — the TypeScript template escaped only `issue_summary`, which is why
    `handoff.py` escapes the packet before it ever reaches a template. Forms and outreach
    rebuild the shared template; this route moves to it when they land.
    """
    rows = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;font-size:13px;font-weight:600;">{label}</td>'
        f'<td style="padding:6px 0;font-size:13px;">{escape_html(str(value) or "—")}</td></tr>'
        for label, value in (
            ("Name", lead["name"]),
            ("Email", lead["email"]),
            ("Phone", lead["phone"] or "—"),
            ("Company", lead["company"] or "—"),
            ("Industry", lead["industry"] or "—"),
            ("Company Size", lead["company_size"] or "—"),
            ("Preferred Contact", lead["preferred_contact"] or "—"),
            ("Preferred Time", lead["preferred_time"] or "—"),
        )
    )
    summary = escape_html(str(lead["issue_summary"])) or "Not provided"
    return (
        "<p>ADMIN ALERT</p>"
        "<h2>New Consultation Request</h2>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{rows}</table>'
        "<p><strong>Issue Summary</strong></p>"
        f'<div style="white-space:pre-wrap;">{summary}</div>'
        "<p>Reply directly to this lead within 1 business day.</p>"
    )


def _send_consultation_alert(lead: dict[str, Any]) -> None:
    try:
        email.send(
            to=_alert_recipient(),
            subject=f"New Consultation Request — {lead['name']} from {lead['company']}",
            html=_consultation_alert_html(lead),
        )
    except Exception as err:  # noqa: BLE001 — the lead is already stored; alerting is best effort
        log.error("setu handoff sendConsultationAlert failed: %s", err)


def _alert_recipient() -> str:
    from app.core.config import settings  # noqa: PLC0415 — keeps import-time side effects out

    return settings.ADMIN_EMAIL or settings.FIRST_ADMIN_EMAIL


@router.post("/handoff")
async def handoff(request: Request, session: SessionDep, background: BackgroundTasks) -> Response:
    """Consented consultation handoff from the Setu widget (outcome-layer §6.4).

    This is the only place the widget touches personal data, so it is built to be the
    demonstration case for what SaralPrivacy sells: purpose stated at the point of
    collection, unticked consent, three fields, and a consent-log row stamped with the
    notice version in force.

    Failure posture differs from /chat/feedback on purpose. Feedback degrades silently
    because a lost thumbs-up is nothing; a lost LEAD is worse than a visible error, so this
    route surfaces failures and the widget falls back to /contact.
    """
    body = _json_body(await request.body())
    if body is None:
        return _error("Invalid JSON body.", 400)

    # Two ceilings, because one does not fit both jobs. A single tight limit checked up
    # front counts rejected requests too, so a visitor who mistypes their email three times
    # is locked out for ten minutes and cannot ask for the callback they wanted. So: a
    # generous ceiling on raw requests stops hammering, and a tight one consumed only by a
    # submission that is actually about to create a lead stops lead flooding. Typos cost
    # the first budget, never the second.
    ip = client_ip(request)
    burst = hit(session, f"chat-handoff-req:{ip}", 15, 10 * 60)
    if not burst.ok:
        return _error(
            "Too many requests. Please wait a moment and try again.",
            429,
            headers={"Retry-After": str(burst.retry_after)},
        )

    # Only bots fill the hidden field. Pretend success, store nothing — same behaviour as
    # /forms/contact so the two are indistinguishable to a prober.
    honeypot = body.get(HONEYPOT_FIELD)
    if isinstance(honeypot, str) and honeypot.strip():
        return JSONResponse({"ok": True})

    valid = validate_handoff_input(body)
    if not valid.ok or valid.contact is None:
        return _error(valid.error or "Consent is required.", 400)

    session_id = _str_field(body, "sessionId", 64) or ""
    if not session_id:
        return _error("sessionId is required.", 400)

    page_url = _str_field(body, "pageUrl", 200) or ""
    raw_reason = body.get("reason")
    reason: EscalationReason = raw_reason if is_escalation_reason(raw_reason) else "explicit_ask"
    last_user_message = _str_field(body, "lastUserMessage", MAX_MESSAGE_CHARS) or ""

    # Past validation, so this request is about to become a lead. Only now does it consume
    # the spam ceiling.
    submit = hit(session, f"chat-handoff-submit:{ip}", 3, 10 * 60)
    if not submit.ok:
        return _error(
            "Too many requests. Please wait a moment and try again.",
            429,
            headers={"Retry-After": str(submit.retry_after)},
        )

    state = sanitize_state(body.get("state"), session_id, page_url)
    packet = build_handoff_packet(
        state=state, page_url=page_url, reason=reason, last_user_message=last_user_message
    )

    city = client_geo(request).city
    country = client_geo(request).country
    region = client_geo(request).region
    user_agent = request.headers.get("user-agent") or ""

    lead_data: dict[str, Any] = {
        "name": valid.contact.name,
        "email": valid.contact.email,
        "phone": "",
        "company": "",
        "industry": packet.industry or "",
        "company_size": "",
        "source": "setu_handoff",
        "issue_summary": (
            f"{packet.summary}\n\nUnresolved: {packet.unresolved_question}\n\n"
            f"Pages shown: {', '.join(packet.sources_shown) or 'none'}\nTrigger: {packet.reason}"
        ),
        "preferred_contact": "email",
        "preferred_time": "",
        "consent_version": PRIVACY_NOTICE_VERSION,
        "risk_level": "",
        "created_at": packet.ts,
        "ip_address": ip,
        "city": city,
        "country": country,
        "region": region,
    }

    try:
        crud.insert_lead(session, lead_data)
    except SQLAlchemyError as err:
        session.rollback()
        log.error("setu handoff lead write failed: %s", err)
        return _error("We couldn't send that just now. Please use the contact page.", 500)

    # Consent record. Non-blocking for the caller: the lead is already safe, and losing the
    # log row must not cost the user their callback — but it is logged loudly because it is
    # the record that matters if anyone ever asks.
    try:
        crud.insert_consent_log(
            session,
            {
                "email": valid.contact.email,
                "name": valid.contact.name,
                "source": "setu_handoff",
                "consent_type": "data_processing",
                "consent_value": True,
                "privacy_version": PRIVACY_NOTICE_VERSION,
                "ip_address": ip,
                "user_agent": user_agent,
                "city": city,
                "country": country,
                "region": region,
                "timestamp": packet.ts,
            },
        )
    except SQLAlchemyError as err:
        session.rollback()
        log.error("setu handoff consent_log write failed: %s", err)

    background.add_task(_send_consultation_alert, lead_data)
    return JSONResponse({"ok": True})


# ── GET /api/v1/chat/health ───────────────────────────────────────────────────
@router.get("/health")
def health() -> Response:
    try:
        engine = retrieval.load_index()
        # Pinecone is the primary retrieval path (D7); report it so a bad key or an empty
        # namespace is visible without reading logs.
        configured = retrieval.is_pinecone_configured()
        stats = retrieval.pinecone_stats() if configured else None
    except Exception as err:  # noqa: BLE001 — health must answer, not raise
        return JSONResponse({"ok": False, "error": str(err)}, status_code=500)
    return JSONResponse(
        {
            "ok": True,
            "chunks": len(engine.chunks),
            "pinecone": {
                "configured": configured,
                "index": retrieval.INDEX_NAME,
                "records": stats["vectorCount"] if stats else None,
                "reachable": stats is not None,
            },
        }
    )


__all__ = ["iso_ms", "router", "sanitize_history"]
