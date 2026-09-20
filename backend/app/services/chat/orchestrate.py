"""Turn orchestration — the deterministic brain around the model.

Rewrite of ``frontend/lib/chat/orchestrate.ts``.

The model produces TEXT ONLY; everything in :class:`ChatMeta` (citations, actions,
confidence, follow-ups) is built HERE from typed data and validated through
:mod:`app.services.chat.site_routing`. A model cannot emit a URL the router doesn't know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.services import retrieval
from app.services.chat import site_routing
from app.services.chat.journeys import ChatSessionState, detect_journey, journey_by_id
from app.services.chat.knowledge import GlossaryHit, GlossaryLookup, lookup_glossary
from app.services.chat.redact import redact
from app.services.chat.site_routing import Route
from app.services.retrieval import RetrievalResult

DISCLAIMER = "Educational only — not legal advice."

EscalationReason = Literal["explicit_ask", "journey_stalled", "repeat_refusal", "negative_feedback"]
Confidence = Literal["high", "low"]
AnimationState = Literal["pointing", "unsure", "speaking"]


@dataclass(frozen=True)
class ChatCitation:
    title: str
    url: str
    tier: int

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url": self.url, "tier": self.tier}


@dataclass(frozen=True)
class ChatAction:
    label: str
    url: str
    type: str = "open_url"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "label": self.label, "url": self.url}


@dataclass
class ChatMeta:
    """The phase-B object. Key order matches the TypeScript literal so a diff of two
    recorded responses stays readable; ``journey``/``industry``/``escalation`` are omitted
    when absent, exactly as ``JSON.stringify`` dropped ``undefined``."""

    citations: list[ChatCitation] = field(default_factory=list)
    actions: list[ChatAction] = field(default_factory=list)
    confidence: Confidence = "low"
    refusal: bool = False
    pii_warning: bool = False
    suggested_followups: list[str] = field(default_factory=list)
    journey: str | None = None
    industry: str | None = None
    animation_state: AnimationState = "pointing"
    disclaimer: str = DISCLAIMER
    escalation_reason: EscalationReason | None = None

    def to_dict(self, sig: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "citations": [c.to_dict() for c in self.citations],
            "actions": [a.to_dict() for a in self.actions],
            "confidence": self.confidence,
            "refusal": self.refusal,
            "piiWarning": self.pii_warning,
            "suggestedFollowups": list(self.suggested_followups),
        }
        if self.journey:
            out["journey"] = self.journey
        if self.industry:
            out["industry"] = self.industry
        out["animation"] = {"state": self.animation_state}
        out["disclaimer"] = self.disclaimer
        if self.escalation_reason:
            out["escalation"] = {"reason": self.escalation_reason}
        if sig is not None:
            out["sig"] = sig
        return out


# ── Industry detection ────────────────────────────────────────────────────────
INDUSTRY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("ca-firms", ("ca firm", "chartered accountant", "accounting firm", "tax practice")),
    ("recruitment-agencies", ("recruitment", "staffing", "hiring agency", "candidates", "ats")),
    ("training-institutes", ("training institute", "coaching", "edtech", "institute")),
    ("d2c-brands", ("d2c", "ecommerce", "online store", "shopify", "brand")),
    ("clinics-diagnostic-labs", ("clinic", "diagnostic", "lab", "patient", "hospital")),
    ("schools-colleges", ("school", "college", "student", "university")),
    ("law-firms", ("law firm", "advocate", "legal practice", "lawyer")),
    ("real-estate", ("real estate", "property", "builder", "broker", "rera")),
    ("hotels-travel", ("hotel", "travel", "guest", "resort", "booking")),
    ("pharmacies", ("pharmacy", "chemist", "prescription", "medicine")),
    ("fintech-nbfc", ("fintech", "nbfc", "lending", "loan", "credit")),
    ("gyms-salons-spas", ("gym", "salon", "spa", "fitness", "member")),
]

_INDUSTRY_FROM_PAGE_RE = re.compile(r"/industries/([a-z0-9-]+)")


def mentions_word(haystack: str, phrase: str) -> bool:
    """Whole-word match. Substring matching silently mis-fires — "ats" (applicant tracking
    system) matches inside "whatsapp", so every WhatsApp question was being classified as a
    recruitment agency until the hybrid eval caught it."""
    escaped = re.escape(phrase)
    return re.search(rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)", haystack, re.I) is not None


def detect_industry(
    message: str, state: ChatSessionState, page_url: str | None = None
) -> str | None:
    slugs = site_routing.industry_slugs()
    if state.industry and state.industry in slugs:
        return state.industry
    m = f" {message.lower()} "
    for slug, words in INDUSTRY_KEYWORDS:
        if any(mentions_word(m, w) for w in words):
            return slug
    if page_url:
        found = _INDUSTRY_FROM_PAGE_RE.search(page_url)
        if found and found.group(1) in slugs:
            return found.group(1)
    return None


# ── Intent regexes ────────────────────────────────────────────────────────────
# Meta-questions about the platform itself — phrased in words that share no vocabulary
# with content ("help me navigate", "what can you do"). These must NEVER refuse: they
# ground on the platform-guide chunks deterministically.
NAV_INTENT_RE = re.compile(
    r"\b(navigat(e|ing|ion)|show me around|(how|what) (do|does|can|is) (i|you|this|the)? ?"
    r"(use|do|work)?,? ?(this|the|your)? ?(site|website|platform|app|tool)s?\b|"
    r"what can you (do|help)|help me (get started|start|use)|getting started|"
    r"guide me (through|around)|give me a tour|what is this (site|website|platform))"
)

ESCALATE_RE = re.compile(
    r"\b(talk to (a |an )?(human|person|someone|expert)|speak (to|with) (a |an )?"
    r"(human|person|someone|expert)|human (help|expert)|privacy expert|need a lawyer|"
    r"legal opinion|consultation with)\b"
)


def router_rescue(message: str) -> list[Route]:
    """Router rescue: an exact trigger phrase in the message is navigation-grade evidence
    even when BM25 scores low (e.g. the single-term "what is dpdpa")."""
    m = message.lower()
    matches = [r for r in site_routing.routes() if any(trig in m for trig in r.triggers)]
    return sorted(matches, key=lambda r: r.tier)


# ── Plan ──────────────────────────────────────────────────────────────────────
@dataclass
class TurnPlan:
    retrieval: RetrievalResult
    glossary: GlossaryLookup
    router_routes: list[Route]
    nav_intent: bool
    escalate: bool
    refuse: bool
    pii_warning: bool
    journey: str | None = None
    industry: str | None = None
    #: Set when the human door should open this turn (§6.1).
    escalation_reason: EscalationReason | None = None
    retrieval_source: Literal["hybrid", "lexical"] = "lexical"


def plan_turn(
    message: str,
    state: ChatSessionState,
    page_url: str | None = None,
    *,
    precomputed_retrieval: RetrievalResult | None = None,
) -> TurnPlan:
    """Pre-model planning: retrieval + tools + the refusal decision.

    ``precomputed_retrieval`` is the fused Pinecone result when the vector store answered;
    absent it, the lexical index is used — the automatic fallback if Pinecone is down or
    unset.
    """
    industry = detect_industry(message, state, page_url)
    journey = detect_journey(message, state.journey)
    lower = message.lower()
    nav_intent = NAV_INTENT_RE.search(lower) is not None
    result = (
        precomputed_retrieval
        if precomputed_retrieval is not None
        else retrieval.retrieve(message, industry=industry, page_url=page_url, top_k=6)
    )
    # Platform meta-questions ground on the platform guide, not lexical overlap.
    if nav_intent and result.confidence == "low":
        result = retrieval.platform_tour()
    glossary = lookup_glossary(message)
    router_routes = router_rescue(message)
    escalate = ESCALATE_RE.search(lower) is not None
    pii_warning = redact(message).redactions > 0

    # Spec §4.2: below floor → refuse, no model-memory fill-in. A confident glossary hit or
    # an exact router-trigger match rescues the turn.
    refuse = (
        result.confidence == "low" and glossary.best is None and not router_routes and not escalate
    )

    return TurnPlan(
        retrieval=result,
        glossary=glossary,
        router_routes=router_routes,
        nav_intent=nav_intent,
        escalate=escalate,
        refuse=refuse,
        pii_warning=pii_warning,
        journey=journey,
        industry=industry,
        escalation_reason=detect_escalation(
            escalate=escalate, refuse=refuse, journey=journey, state=state
        ),
    )


def detect_escalation(
    *, escalate: bool, refuse: bool, journey: str | None, state: ChatSessionState
) -> EscalationReason | None:
    """Outcome-layer §6.1. Three server-visible triggers, checked in order of how certain
    they are that a person is actually wanted. The fourth (negative_feedback) originates
    from a 👎 in the client and arrives by its own path.

    The offer-discipline gate comes first and is absolute: once a session has been offered
    the human door, it is never offered again. Setu asking twice is Setu selling, which
    §2.1 forbids.
    """
    if state.handoff_offered:
        return None

    if escalate:
        return "explicit_ask"

    # Two refusals back to back: the corpus does not cover what they need.
    if refuse and state.consecutive_refusals >= 1:
        return "repeat_refusal"

    # Same journey, still missing a slot, for a second turn running.
    if journey and state.stalled_slot_turns >= 1:
        missing = any(s not in state.facts_confirmed for s in journey_by_id(journey).slots)
        if missing:
            return "journey_stalled"

    return None


def plan_turn_hybrid(
    message: str, state: ChatSessionState, page_url: str | None = None
) -> TurnPlan:
    """Production entry point: Pinecone semantic search + rerank as the primary retrieval
    path (D7), with the local lexical index as an automatic fallback whenever Pinecone is
    unset, unreachable, or returns nothing."""
    industry = detect_industry(message, state, page_url)
    vector = retrieval.pinecone_search(message, industry=industry)
    lexical = retrieval.retrieve(message, industry=industry, page_url=page_url, top_k=8)
    fused = (
        retrieval.fuse_retrieval(vector, lexical, industry=industry, page_url=page_url, top_k=6)
        if vector
        else None
    )
    plan = plan_turn(message, state, page_url, precomputed_retrieval=fused)
    plan.retrieval_source = "hybrid" if vector else "lexical"
    return plan


# ── Grounding block ───────────────────────────────────────────────────────────
def build_grounding_block(plan: TurnPlan) -> str:
    """Grounding block injected per turn (spec §5.3)."""
    chunks = "\n\n".join(
        f"[chunk — source: https://saralprivacy.com{h.chunk.url} · {h.chunk.title} · "
        f"{h.chunk.section}]\n{h.chunk.text}"
        for h in plan.retrieval.hits
    )
    best: GlossaryHit | None = plan.glossary.best
    glossary_block = (
        f"\n<glossary_match>\n{best.term} ({best.section}): {best.definition}\n</glossary_match>"
        if best
        else ""
    )
    journey_block = (
        f"\n<journey>{plan.journey} — {journey_by_id(plan.journey).name}; "
        f"destination {journey_by_id(plan.journey).completion_url}</journey>"
        if plan.journey
        else ""
    )
    industry_block = f"\n<industry>{plan.industry}</industry>" if plan.industry else ""
    return (
        f"<retrieved_context>\n{chunks}\n</retrieved_context>"
        f"{glossary_block}{journey_block}{industry_block}"
    )


# ── Meta construction ─────────────────────────────────────────────────────────
class _HasUrl(Protocol):
    @property
    def url(self) -> str: ...


def _dedupe_by_url[T: _HasUrl](items: list[T]) -> list[T]:
    seen: set[str] = set()
    out: list[T] = []
    for i in items:
        if i.url not in seen:
            seen.add(i.url)
            out.append(i)
    return out


def build_citations(plan: TurnPlan) -> list[ChatCitation]:
    from_router = [
        ChatCitation(title=r.title, url=r.url, tier=r.tier) for r in plan.router_routes[:1]
    ]
    from_retrieval = [
        ChatCitation(title=h.chunk.title, url=h.chunk.url, tier=h.chunk.tier)
        for h in plan.retrieval.hits
    ]
    from_glossary = (
        [ChatCitation(title="DPDPA Glossary", url="/glossary", tier=1)]
        if plan.glossary.best
        else []
    )
    merged: list[ChatCitation] = _dedupe_by_url([*from_router, *from_glossary, *from_retrieval])
    return [c for c in merged if site_routing.is_valid_citation(c.url)][:3]


def build_actions(plan: TurnPlan) -> list[ChatAction]:
    actions: list[ChatAction] = []

    def push(route: Route | None, label: str | None = None) -> None:
        if route and site_routing.is_valid_citation(route.url):
            actions.append(ChatAction(label=label or route.title, url=route.url))

    if plan.refuse:
        push(site_routing.route_by_url("/faq"), "Open the FAQ")
        push(site_routing.route_by_url("/learn"), "Browse the Learning Hub")
        actions.append(ChatAction(label="Contact SaralPrivacy", url="/contact"))
        return actions

    # Platform tour: the journey's first steps as Open cards.
    if plan.nav_intent:
        push(site_routing.route_by_url("/discovery"), "Start with Data Discovery")
        push(site_routing.route_by_url("/data-mapping"))
        push(site_routing.route_by_url("/assessment"))
        return _dedupe_by_url(actions)[:3]

    # Escalation intent: the human door leads (spec §6 escalation).
    if plan.escalate:
        actions.append(ChatAction(label="Talk to a human — Contact SaralPrivacy", url="/contact"))
    # Exact router-trigger match: that destination is the answer.
    if plan.router_routes:
        push(plan.router_routes[0])
    # Journey destination first.
    if plan.journey:
        j = journey_by_id(plan.journey)
        label = (
            "Map my data" if j.id == "J2" else "Start the readiness check" if j.id == "J3" else None
        )
        push(site_routing.route_by_url(j.completion_url), label)
    # Industry guide when known.
    if plan.industry:
        push(site_routing.route_for_industry(plan.industry))
    # Top authority citation as an Open card.
    if plan.retrieval.hits:
        push(site_routing.route_by_url(plan.retrieval.hits[0].chunk.url))

    return _dedupe_by_url(actions)[:3]


FOLLOWUP_BY_JOURNEY: dict[str, list[str]] = {
    "J1": ["What counts as digital personal data?", "Where should my business begin?"],
    "J2": ["What are the biggest risk hotspots for my industry?", "Check my readiness"],
    "J3": ["What personal data does my business handle?", "What should I fix first?"],
    "J4": ["What must my privacy notice say?", "What makes consent valid?"],
    "J5": ["What rights do my customers have?", "Do I need consent for marketing?"],
    "J6": ["Show me the key DPDPA terms", "How does this apply to my business?"],
}

REFUSAL_FOLLOWUPS = ["What is DPDPA?", "Does DPDPA apply to me?"]
NAV_FOLLOWUPS = [
    "What tools does SaralPrivacy offer?",
    "Where should my business begin?",
    "What does DPDPA mean for my industry?",
]
DEFAULT_FOLLOWUPS = ["Does DPDPA apply to me?", "Where should my business begin?"]


def build_followups(plan: TurnPlan) -> list[str]:
    if plan.refuse:
        return list(REFUSAL_FOLLOWUPS)
    if plan.nav_intent:
        return list(NAV_FOLLOWUPS)
    if plan.journey:
        return list(FOLLOWUP_BY_JOURNEY[plan.journey])
    tags = plan.retrieval.hits[0].chunk.topic_tags if plan.retrieval.hits else []
    if "consent" in tags:
        return list(FOLLOWUP_BY_JOURNEY["J4"])
    return list(DEFAULT_FOLLOWUPS)


def build_meta(plan: TurnPlan) -> ChatMeta:
    refusal = plan.refuse
    rescued = bool(plan.router_routes) or plan.glossary.best is not None or plan.escalate
    confidence: Confidence = (
        "low" if refusal else ("high" if rescued else plan.retrieval.confidence)
    )
    return ChatMeta(
        citations=[] if refusal else build_citations(plan),
        actions=build_actions(plan),
        confidence=confidence,
        refusal=refusal,
        pii_warning=plan.pii_warning,
        suggested_followups=build_followups(plan),
        journey=plan.journey,
        industry=plan.industry,
        animation_state="unsure" if refusal else "pointing",
        disclaimer=DISCLAIMER,
        escalation_reason=plan.escalation_reason,
    )


def guarded_meta() -> ChatMeta:
    """Meta for a turn refused by the injection guard — no citations, no leads."""
    return ChatMeta(
        citations=[],
        actions=[
            ChatAction(label="Open the FAQ", url="/faq"),
            ChatAction(label="Contact SaralPrivacy", url="/contact"),
        ],
        confidence="low",
        refusal=True,
        pii_warning=False,
        suggested_followups=list(REFUSAL_FOLLOWUPS),
        animation_state="unsure",
        disclaimer=DISCLAIMER,
    )
