"""Named journeys J1–J6 (spec §3.2) and the untrusted-state sanitiser.

Rewrite of the server half of ``frontend/lib/chat/journeys.ts``. The widget keeps the
same table client-side for its memory panel; the two must agree on ids and names, so the
journey list below is copied verbatim.

``ChatSessionState`` arrives from the browser on every request — it is a claim, never a
record — so :func:`sanitize_state` rebuilds it field by field with hard ceilings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.chat.site_routing import industry_slugs

JOURNEY_IDS = ("J1", "J2", "J3", "J4", "J5", "J6")
USER_TYPES = ("owner", "employee", "consultant", "individual")


@dataclass(frozen=True)
class Journey:
    id: str
    name: str
    entry_keywords: tuple[str, ...]  # lowercase substring match
    slots: tuple[str, ...]  # slot names, asked one at a time when missing
    completion_url: str  # the destination that completes the journey


JOURNEYS: tuple[Journey, ...] = (
    Journey(
        id="J1",
        name="Does DPDPA apply to me?",
        entry_keywords=(
            "apply to",
            "applies to",
            "am i covered",
            "applicab",
            "exempt",
            "does the law",
        ),
        slots=("orgType", "handlesDigitalData", "whoseData"),
        completion_url="/learn/applicability",
    ),
    Journey(
        id="J2",
        name="What personal data does my business handle?",
        entry_keywords=(
            "what data",
            "data do we",
            "data map",
            "ropa",
            "inventory",
            "data flow",
            "where does data",
        ),
        slots=("industry",),
        completion_url="/discovery",
    ),
    Journey(
        id="J3",
        name="Where should my business begin?",
        entry_keywords=(
            "where do i start",
            "where to start",
            "begin",
            "readiness",
            "where do we stand",
            "assessment",
        ),
        slots=("industry",),
        completion_url="/assessment",
    ),
    Journey(
        id="J4",
        name="Do I need consent for this?",
        entry_keywords=("consent", "opt-in", "opt in", "permission to", "checkbox"),
        slots=("channel", "purpose"),
        completion_url="/learn/consent",
    ),
    Journey(
        id="J5",
        name="Create or improve a privacy notice",
        entry_keywords=("privacy notice", "privacy policy", "notice generator", "draft a notice"),
        slots=(),
        completion_url="/tools/dpdpa-privacy-notice-generator",
    ),
    Journey(
        id="J6",
        name="What does this term or rule mean?",
        entry_keywords=("what does", "what is a", "meaning of", "define", "definition of", "term"),
        slots=(),
        completion_url="/glossary",
    ),
)


def detect_journey(message: str, current: str | None = None) -> str | None:
    m = message.lower()
    for j in JOURNEYS:
        if any(k in m for k in j.entry_keywords):
            return j.id
    return current  # stay in the active journey when the turn doesn't pivot


def journey_by_id(journey_id: str) -> Journey:
    return next(j for j in JOURNEYS if j.id == journey_id)


@dataclass
class ChatSessionState:
    session_id: str
    entry_page_url: str
    intent: str | None = None
    user_type: str | None = None
    industry: str | None = None
    business_niche: str | None = None
    journey: str | None = None
    journey_stage: str | None = None
    facts_confirmed: dict[str, str] = field(default_factory=dict)
    last_topic: str | None = None
    pages_shown: list[str] = field(default_factory=list)
    message_count: int = 0
    consent_to_contact: bool = False
    # Escalation memory (outcome-layer spec §6.1). The server holds no transcript, so
    # turn-to-turn signals ride in the client state that already round-trips on every
    # request. These are HINTS, not authority: the worst a tampered value can do is open
    # the human door a turn early, which is harmless. Both are clamped below.
    consecutive_refusals: int = 0  # reset to 0 on any answered turn
    stalled_slot_turns: int = 0  # reset to 0 when the journey moves or a slot fills
    #: One handoff offer per session — set once offered, never re-offered (§2.1).
    handoff_offered: bool = False


def create_initial_state(session_id: str, entry_page_url: str) -> ChatSessionState:
    return ChatSessionState(session_id=session_id, entry_page_url=entry_page_url)


def _counter(v: Any, maximum: int = 10) -> int:
    """Clamp an untrusted client counter into a small non-negative integer."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return 0
    try:
        return min(max(int(v), 0), maximum)
    except (ValueError, OverflowError):
        return 0


def _str(v: Any, maximum: int = 120) -> str | None:
    return v[:maximum] if isinstance(v, str) else None


def sanitize_state(raw: Any, session_id: str, page_url: str) -> ChatSessionState:
    """Sanitize an untrusted client-supplied state object into a safe shape."""
    base = create_initial_state(session_id, page_url)
    if not isinstance(raw, dict):
        return base

    facts: dict[str, str] = {}
    raw_facts = raw.get("factsConfirmed")
    if isinstance(raw_facts, dict):
        for k, v in list(raw_facts.items())[:20]:
            val = _str(v, 200)
            if val:
                facts[str(k)[:40]] = val

    pages = raw.get("pagesShown")
    base.intent = _str(raw.get("intent"))
    user_type = raw.get("userType")
    base.user_type = user_type if user_type in USER_TYPES else None
    base.industry = _str(raw.get("industry"))
    base.business_niche = _str(raw.get("businessNiche"))
    journey = raw.get("journey")
    base.journey = journey if journey in JOURNEY_IDS else None
    base.journey_stage = _str(raw.get("journeyStage"))
    base.facts_confirmed = facts
    base.last_topic = _str(raw.get("lastTopic"))
    base.pages_shown = (
        [p for p in pages if isinstance(p, str)][:30] if isinstance(pages, list) else []
    )
    count = raw.get("messageCount")
    base.message_count = (
        min(int(count), 500)
        if isinstance(count, int | float) and not isinstance(count, bool)
        else 0
    )
    base.consent_to_contact = raw.get("consentToContact") is True
    base.consecutive_refusals = _counter(raw.get("consecutiveRefusals"))
    base.stalled_slot_turns = _counter(raw.get("stalledSlotTurns"))
    base.handoff_offered = raw.get("handoffOffered") is True
    return base


def is_known_industry(slug: str | None) -> bool:
    return slug is not None and slug in industry_slugs()
