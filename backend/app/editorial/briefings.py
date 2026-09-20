"""Briefing creation: the pipeline/manual payload path and the Claude auto-generate path.

Rewritten from `frontend/app/api/briefings/generate/route.ts`. Both paths insert a briefing
with `status="approved"` (live on the site immediately); the auto path also emails the
admins an approval link that broadcasts to subscribers.
"""

import base64
import json
import logging
import math
import re
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, TypedDict

import httpx
from sqlmodel import Session

from app.core.config import settings
from app.crud import editorial as crud
from app.editorial import emails
from app.editorial.docs import briefing_doc, briefing_slug, js_json, js_round, word_count
from app.models.editorial import Briefing
from app.services import llm

log = logging.getLogger(__name__)

AUTO_MODEL = "claude-opus-4-5"
AUTO_MAX_TOKENS = 2048
DEFAULT_AUTHOR = "DPDPA Editorial Team"
IST = timezone(timedelta(hours=5, minutes=30))


class Topic(TypedDict):
    category: str
    topic: str


class Theme(TypedDict):
    type: str
    label: str
    tone: str


# ─── Daily DPDPA topic rotation ────────────────────────────────────────────
DPDPA_TOPICS: list[Topic] = [
    {
        "category": "consent-management",
        "topic": "Consent notice requirements under DPDPA Section 6 — what your forms must say from Day 1",
    },
    {
        "category": "sector-specific",
        "topic": "Recruitment agencies and CV databases — DPDPA obligations for candidate data handling",
    },
    {
        "category": "sector-specific",
        "topic": "CA firms and tax data — DPDPA compliance for PAN, Aadhaar, and ITR data processing",
    },
    {
        "category": "consent-management",
        "topic": "WhatsApp marketing and DPDPA — consent requirements for D2C and e-commerce brands",
    },
    {
        "category": "regulatory-update",
        "topic": "Data breach notification under DPDPA — timelines, scope, and what businesses must report",
    },
    {
        "category": "sector-specific",
        "topic": "Training institutes and EdTech — student and parent data protection under DPDPA Section 9",
    },
    {
        "category": "data-rights",
        "topic": "Rights of Data Principals under DPDPA — access, correction, erasure, and nomination rights",
    },
    {
        "category": "compliance-guidance",
        "topic": "Significant Data Fiduciary criteria — how to know if your business qualifies and what changes",
    },
    {
        "category": "enforcement",
        "topic": "DPDPA penalties under Section 33 — fines up to ₹250 crore and what triggers them",
    },
    {
        "category": "consent-management",
        "topic": "Children's data under DPDPA Section 9 — parental consent and age verification requirements",
    },
    {
        "category": "compliance-guidance",
        "topic": "Grievance redressal mechanism under Section 13 — what your business must provide",
    },
    {
        "category": "sector-specific",
        "topic": "Healthcare and patient data — DPDPA compliance for hospitals, clinics, and healthtech",
    },
    {
        "category": "regulatory-update",
        "topic": "Cross-border data transfers under DPDPA — approved countries and restricted jurisdictions",
    },
    {
        "category": "compliance-guidance",
        "topic": "Data retention and deletion — how long to keep personal data under DPDPA",
    },
    {
        "category": "sector-specific",
        "topic": "Fintech and digital lending — DPDPA compliance for NBFCs, lending apps, and UPI platforms",
    },
    {
        "category": "compliance-guidance",
        "topic": "Employee HR data privacy — DPDPA obligations for Indian employers and HR departments",
    },
    {
        "category": "compliance-guidance",
        "topic": "Third-party vendors and data processors — DPDPA obligations beyond your own systems",
    },
    {
        "category": "regulatory-update",
        "topic": "DPDPA Rules notification status — what to expect from the government in 2025-26",
    },
    {
        "category": "consent-management",
        "topic": "E-commerce checkout and DPDPA — customer consent, order data, and return processes",
    },
    {
        "category": "compliance-guidance",
        "topic": "DPDPA audit trail and record-keeping — what documentation every business must maintain",
    },
    {
        "category": "consent-management",
        "topic": "Withdrawing consent under DPDPA Section 6(4) — building the right mechanism for users",
    },
    {
        "category": "data-rights",
        "topic": "Data correction and erasure rights under Section 12 — implementing right to be forgotten",
    },
    {
        "category": "sector-specific",
        "topic": "Real estate and DPDPA — buyer data, broker databases, and PropTech compliance",
    },
    {
        "category": "compliance-guidance",
        "topic": "Small business DPDPA compliance — a practical 90-day roadmap for Indian SMEs",
    },
    {
        "category": "consent-management",
        "topic": "Newsletter and email marketing under DPDPA — consent, unsubscribe, and record-keeping",
    },
    {
        "category": "sector-specific",
        "topic": "Hospitality and hotels — guest data, loyalty programs, and DPDPA compliance",
    },
    {
        "category": "compliance-guidance",
        "topic": "Website privacy policy update checklist for DPDPA 2023 compliance",
    },
    {
        "category": "regulatory-update",
        "topic": "Data Protection Board of India — structure, powers, and how complaints will be handled",
    },
    {
        "category": "sector-specific",
        "topic": "Manufacturing and DPDPA — employee biometrics, CCTV, and shop-floor data",
    },
    {
        "category": "compliance-guidance",
        "topic": "DPDPA vs GDPR — key differences for Indian businesses expanding globally",
    },
]

# ─── Day-of-week theme system (index 0 = Sunday, as Date.getDay()) ─────────
DOW_THEMES: list[Theme] = [
    {
        "type": "recap",
        "label": "Weekly Recap",
        "tone": "Summarise the key DPDPA lesson for the week in a story-style recap — what should readers take away from this week?",
    },
    {
        "type": "concept",
        "label": "Big Idea",
        "tone": "Explain one core DPDPA concept in the simplest possible language for a shop owner, HR manager, or SME founder.",
    },
    {
        "type": "example",
        "label": "Business Example",
        "tone": "Walk through a concrete real-world scenario showing exactly how this DPDPA rule plays out in an Indian MSME.",
    },
    {
        "type": "mistake",
        "label": "Common Mistake",
        "tone": "Reveal the single most common DPDPA mistake Indian MSMEs make on this topic and how to fix it immediately.",
    },
    {
        "type": "audit",
        "label": "Mini Audit",
        "tone": "Give readers a quick 5-minute self-check they can do right now to assess their compliance on this topic.",
    },
    {
        "type": "myth",
        "label": "Myth Buster",
        "tone": "Bust a common wrong assumption MSMEs have about this DPDPA rule and reveal the surprising truth.",
    },
    {
        "type": "case",
        "label": "Case Story",
        "tone": "Tell a scenario story: 'Imagine this happened in your company...' Make it feel real and close to home for Indian MSMEs.",
    },
]


def _now() -> datetime:
    return datetime.now(UTC)


def tomorrow_0330_utc(now: datetime) -> datetime:
    """`tomorrow.setDate(+1); setHours(3,30,0,0)` on a UTC server: 09:00 IST tomorrow."""
    return (now + timedelta(days=1)).replace(hour=3, minute=30, second=0, microsecond=0)


# ── manual / pipeline path ────────────────────────────────────────────────
def _join_js(values: list[Any]) -> str:
    """Array.join(" "): null/undefined become empty strings."""
    return " ".join("" if v is None else str(v) for v in values)


def manual_read_time(body: dict[str, Any]) -> int:
    if body.get("read_time"):
        return int(body["read_time"])
    txt = _join_js(
        [
            body.get("title"),
            body.get("excerpt"),
            body.get("summary"),
            body.get("why_it_matters"),
            body.get("business_impact"),
            _join_js(list(body.get("who_is_affected") or [])),
            _join_js(list(body.get("action_checklist") or [])),
        ]
    )
    return max(1, js_round(word_count(txt) / 200))


def create_manual_briefing(
    session: Session, body: dict[str, Any], *, now: datetime | None = None
) -> Briefing:
    """POST /briefings/generate with a `title` (the pipeline payload)."""
    now = now or _now()
    date_str = str(body.get("date") or now.date().isoformat())
    slug = briefing_slug(date_str, str(body["title"]))

    # Duplicate guard: a retried same-day submission replaces its own drafts.
    try:
        for existing in crud.find_briefings(session, slug=slug, status="draft", limit=5):
            crud.delete_briefing(session, existing)
    except Exception as e:  # noqa: BLE001 — non-blocking, as before
        log.warning("draft cleanup for %s failed: %s", slug, e)
        session.rollback()

    # The site shows published_at || created_at, so a backfilled day is stamped 09:00 IST
    # of its roadmap date rather than "now".
    created_at = datetime.fromisoformat(f"{date_str}T03:30:00+00:00") if body.get("date") else now

    if body.get("business_impact") or body.get("who_is_affected"):
        why = js_json(
            {
                "why": body.get("why_it_matters") or "",
                "impact": body.get("business_impact") or "",
                "affected": body.get("who_is_affected") or [],
            }
        )
    else:
        why = str(body.get("why_it_matters") or "")

    briefing = Briefing(
        title=str(body["title"]),
        slug=slug,
        excerpt=str(body.get("excerpt") or ""),
        summary=str(body.get("summary") or ""),
        why_it_matters=why,
        action_checklist=js_json(body.get("action_checklist") or []),
        category=str(body.get("category") or "compliance-guidance"),
        tags=js_json(body.get("tags") or []),
        industries=js_json(body.get("industries") or ["general"]),
        read_time=manual_read_time(body),
        featured=False,
        author=DEFAULT_AUTHOR,
        status="approved",  # live on the website immediately
        approval_token=str(uuid.uuid4()),
        scheduled_for=tomorrow_0330_utc(now),
        created_at_attr=created_at,
        infographic_base64=str(body.get("infographic_url") or body.get("infographic_base64") or ""),
    )
    return crud.create_briefing(session, briefing)


# ── auto-generate path ────────────────────────────────────────────────────
def pick_topic_and_theme(ref: datetime) -> tuple[Topic, Theme]:
    start_of_year = datetime(ref.year, 1, 1, tzinfo=ref.tzinfo)
    day_of_year = math.floor((ref - start_of_year).total_seconds() / 86_400)
    dow = (ref.weekday() + 1) % 7  # Python Monday=0 → JS Sunday=0
    return DPDPA_TOPICS[day_of_year % len(DPDPA_TOPICS)], DOW_THEMES[dow]


def action_format_for(theme_type: str) -> str:
    if theme_type == "audit":
        return "audit"
    if theme_type in ("case", "recap"):
        return "team"
    if theme_type in ("mistake", "example"):
        return "mistake"
    return "today"


# The TS route sent everything as one user message with no system prompt. The Messages
# wrapper always sends `system`, so the opening role line travels there and the rest of the
# text is unchanged.
AUTO_SYSTEM = (
    "You are the DPDPA Editorial Team at SaralPrivacy — India's plain-language DPDPA "
    "compliance platform for small and medium businesses."
)


def auto_prompt(topic: str, category: str, theme: Theme) -> str:
    return f"""Today's theme: {theme["label"]} — {theme["tone"]}
Topic: "{topic}"

Write a 2-minute daily DPDPA briefing for Indian MSME owners, founders, HR managers, accountants, and operations leads. Write for an 8th-grade reading level. No legal jargon. No "notwithstanding". No "pursuant to". Never start a sentence with "Under Section" — start with pain, risk, or a real business situation.

LEGAL TERM TRANSLATIONS (always use these):
- Data Principal → the person whose data it is
- Data Fiduciary → the business using the data
- Personal data → information that can identify a person
- Processing → collecting, storing, sharing, using, or deleting data
- Consent → clear permission

Return ONLY a valid JSON object (no markdown, no explanation, no extra text):

{{
  "title": "A question-style title that a business owner would actually ask. Max 85 characters. Examples: 'Can You Keep Ex-Employee Records Forever?', 'What Happens If You Share Customer Data Without Permission?'",
  "tag": "One topic label only — one of: Consent / Notice / Penalty / Data Breach / HR Records / Vendor Risk / Children's Data / Employee Data / Marketing / Customer Data / Data Storage / Access Control",
  "hook_line1": "One sharp line with tension, risk, danger, or a surprising reality. Max 18 words. Must NOT start with 'Under Section'. Start with pain or confusion.",
  "hook_line2": "One line stating the business impact in plain language. Max 20 words.",
  "card_what": "Complete one-line plain English meaning of this rule. Max 20 words.",
  "card_why": "Complete one-line risk or business consequence. Max 20 words.",
  "card_action": "Complete one practical action the reader can take. Max 20 words.",
  "card_owner": "Who in the company is responsible — list roles separated by + e.g. Founder + HR + IT",
  "explainer_concept": "The concept explained in very plain language. Max 30 words. Write like you are explaining to a friend.",
  "explainer_example": "A specific real MSME example — a recruitment agency, CA firm, D2C brand, training institute, or general business. Max 30 words.",
  "explainer_mistake": "The most common mistake businesses make on this exact topic. Max 30 words. Start with the mistake, not with 'The mistake is'.",
  "action_format": "{action_format_for(theme["type"])}",
  "action_items": ["Action or question 1 — max 18 words", "Action or question 2 — max 18 words", "Action or question 3 — max 18 words"],
  "save_line": "One sharp, memorable, slightly punchy sentence. This is the one line readers will screenshot and share. Max 25 words.",
  "participation": "One short question that makes readers think about their own business. Max 15 words. Example: 'Would your business pass this check today?'",
  "excerpt": "2 sentences for the card preview on the briefings list page. Max 200 characters.",
  "category": "{category}",
  "tags": ["tag1", "tag2", "tag3"],
  "industries": ["one or more of: recruitment, ca-firms, training-institutes, d2c-brands, healthcare, fintech, general"],
  "featured": false,
  "author": "DPDPA Editorial Team"
}}

Critical rules:
- action_format must be exactly one of: today / team / mistake / audit
- action_items must have EXACTLY 3 items
- All fields must be complete sentences or phrases — no trailing "..."
- Keep every field SHORT — brevity is the whole point
- Write for a person reading on their phone between meetings
- Return ONLY the JSON object"""


_FENCE_START = re.compile(r"^```json\s*", re.IGNORECASE)
_FENCE_END = re.compile(r"```\s*$", re.IGNORECASE)


def parse_claude_json(text: str) -> dict[str, Any]:
    clean = _FENCE_END.sub("", _FENCE_START.sub("", text)).strip()
    data = json.loads(clean)
    if not isinstance(data, dict):
        raise ValueError("Claude did not return a JSON object")
    return data


def _or(raw: dict[str, Any], key: str, default: Any = "") -> Any:
    value = raw.get(key)
    return value if value else default


def build_auto_briefing(
    raw: dict[str, Any], topic: Topic, theme: Theme, ref: datetime, now: datetime
) -> Briefing:
    date_str = ref.date().isoformat()
    why = js_json(
        {
            "version": 2,
            "tag": _or(raw, "tag"),
            "hook_line1": _or(raw, "hook_line1"),
            "hook_line2": _or(raw, "hook_line2"),
            "card_what": _or(raw, "card_what"),
            "card_why": _or(raw, "card_why"),
            "card_action": _or(raw, "card_action"),
            "card_owner": _or(raw, "card_owner"),
            "explainer_concept": _or(raw, "explainer_concept"),
            "explainer_example": _or(raw, "explainer_example"),
            "explainer_mistake": _or(raw, "explainer_mistake"),
            "action_format": _or(raw, "action_format", "today"),
            "action_items": _or(raw, "action_items", []),
            "save_line": _or(raw, "save_line"),
            "participation": _or(raw, "participation"),
            "theme_type": theme["type"],
            "theme_label": theme["label"],
        }
    )
    words = _join_js(
        [
            raw.get("hook_line1"),
            raw.get("hook_line2"),
            raw.get("card_what"),
            raw.get("card_why"),
            raw.get("card_action"),
            raw.get("explainer_concept"),
            raw.get("explainer_example"),
            raw.get("explainer_mistake"),
            *(raw.get("action_items") or []),
            raw.get("save_line"),
        ]
    )
    # `.split(/\s+/).length` counts empty edges too; ceil(words/200) with a floor of 1.
    read_time = max(1, math.ceil(len(re.split(r"\s+", words)) / 200))
    featured = raw.get("featured")
    return Briefing(
        title=str(raw.get("title") or ""),
        slug=briefing_slug(date_str, str(raw.get("title") or "")),
        excerpt=str(_or(raw, "excerpt")),
        summary=str(_or(raw, "save_line")),  # save_line doubles as the email summary
        why_it_matters=why,
        action_checklist=js_json(_or(raw, "action_items", [])),
        category=str(_or(raw, "category", topic["category"])),
        tags=js_json(_or(raw, "tags", [])),
        industries=js_json(_or(raw, "industries", ["general"])),
        featured=bool(featured) if featured is not None else False,
        author=str(_or(raw, "author", DEFAULT_AUTHOR)),
        status="approved",  # live immediately — no manual approval gate
        read_time=read_time,
        approval_token=str(uuid.uuid4()),
        scheduled_for=tomorrow_0330_utc(now),
        created_at_attr=now,
    )


def push_to_github(slug: str, payload: dict[str, Any], now: datetime) -> None:
    """Optional archive copy in a GitHub repo; skipped when not configured. Never raises."""
    token, owner, repo = (
        settings.GITHUB_TOKEN.strip(),
        settings.GITHUB_OWNER.strip(),
        settings.GITHUB_REPO.strip(),
    )
    if not (token and owner and repo):
        return
    path = f"briefings/{now.date().isoformat()}-{slug}.json"
    content = base64.b64encode(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode()
    ).decode()
    try:
        r = httpx.put(
            f"https://api.github.com/repos/{owner}/{repo}/contents/{path}",
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
                "Content-Type": "application/json",
            },
            json={"message": f"chore: daily briefing — {slug}", "content": content},
            timeout=20,
        )
        if r.status_code >= 300:
            log.warning("[briefing] GitHub push failed: %s", r.text[:300])
    except httpx.HTTPError as e:
        log.warning("[briefing] GitHub push failed: %s", e)


def auto_generate(
    session: Session, for_date: str | None, *, now: datetime | None = None
) -> tuple[Briefing, str]:
    """POST /briefings/generate with no `title`: Claude writes today's (or `forDate`'s) briefing."""
    now = now or _now()
    ref = datetime.fromisoformat(f"{for_date}T12:00:00+00:00") if for_date else now
    topic, theme = pick_topic_and_theme(ref)

    text = llm.complete(
        AUTO_SYSTEM,
        [{"role": "user", "content": auto_prompt(topic["topic"], topic["category"], theme)}],
        model=AUTO_MODEL,
        max_tokens=AUTO_MAX_TOKENS,
    )
    raw = parse_claude_json(text)
    briefing = crud.create_briefing(session, build_auto_briefing(raw, topic, theme, ref, now))

    push_to_github(briefing.slug, briefing_doc(briefing, include_private=True), now)
    try:
        emails.send_approval_email(briefing, briefing.approval_token or "")
    except Exception as e:  # noqa: BLE001 — as before: .catch(console.error)
        log.error("approval email failed: %s", e)
    return briefing, topic["topic"]


def today_ist_window(now: datetime) -> tuple[str, datetime, datetime]:
    """Today's IST date and its [00:00:00, 23:59:59] bounds (timezone-aware)."""
    today = now.astimezone(IST).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=IST)
    end = start + timedelta(hours=23, minutes=59, seconds=59)
    return today.isoformat(), start, end


def ist_today(now: datetime | None = None) -> date:
    return (now or _now()).astimezone(IST).date()


__all__ = [
    "DOW_THEMES",
    "DPDPA_TOPICS",
    "auto_generate",
    "create_manual_briefing",
    "ist_today",
    "pick_topic_and_theme",
    "today_ist_window",
]
