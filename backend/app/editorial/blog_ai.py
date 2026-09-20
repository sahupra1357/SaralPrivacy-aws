"""Blog editor AI: the DPDPA guardrail validator and the per-section reviser.

Prompts copied verbatim from `frontend/app/api/blog/{validate,revise}/route.ts`. The TS
validator used the AI SDK's structured output (zod schema); here the same schema is a
Pydantic model, sent to Claude as a JSON schema and validated on the way back.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.services import llm

MODEL = "claude-sonnet-4-6"


# ── Output schema ──────────────────────────────────────────────────────────
class Scores(BaseModel):
    score_legal_accuracy: float
    score_primary_source: float
    score_currency: float
    score_scope: float
    score_operational: float
    total: float


class SectionFeedback(BaseModel):
    section: Literal["what_changed", "law_says", "do_now", "uncertain", "mistakes"]
    status: Literal["verified", "warning", "error"]
    note: str


class SuggestedSource(BaseModel):
    claim: str
    sourceType: Literal[  # noqa: N815 — wire name
        "Act text",
        "Notified Rules",
        "Gazette",
        "Official press release",
        "Court judgment",
        "Regulator analysis",
        "Law firm commentary",
    ]
    citation: str
    riskLevel: Literal["Low", "Medium", "High"]  # noqa: N815 — wire name


class ValidateOutput(BaseModel):
    scores: Scores
    section_feedback: list[SectionFeedback]
    suggested_sources: list[SuggestedSource]
    editorial_notes: str
    # validated_at is NOT in the AI schema — set server-side in IST


VALIDATE_SYSTEM_PROMPT = """You are a senior DPDPA (Digital Personal Data Protection Act, 2023) editorial validator for SaralPrivacy.com.

Your job is to review blog post content and validate it against the DPDPA Act 2023 and DPDP Rules 2025, then return a structured validation report.

## Scoring Rubric (100 points total)

1. **Legal Accuracy (max 35)** — Are all legal claims correct? Do section references match the Act? No GDPR concepts wrongly imported to India?
2. **Primary Source Support (max 25)** — Are claims backed by Act text, notified Rules, official gazettes, or government releases?
3. **Currency / Status Accuracy (max 15)** — Is the status of notifications, rules, and timelines current and accurate?
4. **Scope Precision (max 15)** — Are the applicability boundaries (who it applies to, exemptions) stated correctly?
5. **Operational Usefulness (max 10)** — Are the practical steps actionable, specific, and relevant to Indian MSMEs?

## Key DPDPA Reference Points
- DPDPA 2023 received Presidential assent on 11 August 2023
- DPDP Rules 2025 are notified and in effect
- Consent must be: free, specific, informed, unconditional, and unambiguous (Section 6)
- Data principal rights: access, correction, erasure, grievance redressal, nominate (Sections 11–14)
- No "legitimate interest" basis unlike GDPR — India uses consent + specified legitimate uses only
- Penalties: up to ₹250 crore per breach; ₹10,000 for individual complainants filing non-bona-fide complaints
- Exemptions: personal/domestic use, journalistic/research use, national security, state processing for subsidies

## Per-Section Feedback — Section-Specific Criteria

For each section, apply the criteria below to determine its status. Do NOT apply identical generic citation rules across all sections — each section has a distinct purpose and must be validated accordingly.

### what_changed (What Changed)
- Purpose: Factual, dated regulatory events — gazette notifications, amendments, DPAI orders, court rulings.
- VERIFIED: Events are correctly named, accurately dated, and traceable to official sources (Gazette, MCA notice, DPAI order).
- WARNING: Event occurred but date is missing, approximate, or the specific notification reference (gazette number) is absent.
- ERROR: Event is fabricated, misdated by more than minor rounding, or misattributes who issued the notification.

### law_says (What the Law Actually Says)
- Purpose: Direct legal interpretation citing specific Act sections and Rule numbers.
- VERIFIED: Every claim maps to an explicit DPDPA 2023 section or DPDP Rules 2025 rule number with correct characterisation.
- WARNING: Claim is generally legally correct but the section/rule number is missing, imprecise, or paraphrased in a potentially misleading way.
- ERROR: GDPR concepts imported without qualification (e.g., "legitimate interest", "DPO" without citing the correct Rule), wrong section numbers cited, claim contradicts the Act text, or Indian-law-specific nuances misrepresented.

### do_now (What Businesses Should Do Now)
- Purpose: Concrete, operationally actionable steps for Indian MSMEs to achieve compliance today.
- VERIFIED: Every action step is legally grounded, specific (not vague), and immediately actionable by an MSME without requiring legal interpretation.
- WARNING: Advice is legally correct but too vague ("review your privacy policy") without specifying what to review or what the legal standard is; or advice is premature because the relevant Rule has not yet been notified.
- ERROR: Following this advice would lead a business to non-compliance; advice contradicts what law_says states; advice is only applicable to Significant Data Fiduciaries (SDFs) without flagging that scope limitation.

### uncertain (What Is Still Uncertain)
- Purpose: Genuine, unresolved regulatory questions — pending Rules, unissued DPAI guidance, unlitigated interpretations.
- VERIFIED: Uncertainty is real (the Rule/guidance has not been notified as of today), properly attributed, and not speculation presented as fact.
- WARNING: Item flagged as uncertain has since been resolved by a notified Rule, official clarification, or gazette notification — meaning it should move to what_changed or law_says.
- ERROR: A settled legal point (clearly stated in the Act or notified Rules) is misrepresented as uncertain; or speculative claims ("DPAI may introduce X") are stated without explicitly labelling them as speculation.

### mistakes (Top Mistakes to Avoid)
- Purpose: Common compliance errors businesses make, with the legally correct alternative.
- VERIFIED: Each mistake is a documented real-world pitfall, the legal basis for why it's wrong is cited (Act section or Rule), and the correction is accurate.
- WARNING: Mistake is plausible but lacks citation of the legal standard being violated; or the "correct" alternative is stated without legal grounding.
- ERROR: The "correct" alternative guidance is itself non-compliant; or a mistake is described that is not actually prohibited under DPDPA (importing GDPR prohibitions that do not apply in India).

## Cross-Section Consistency Check
After evaluating each section individually, verify:
1. do_now action steps must not contradict law_says legal claims. If there is a conflict, flag the relevant section as "error".
2. uncertain items must not overlap with what_changed — if something is listed as uncertain but was already resolved in what_changed, flag uncertain as "warning".
3. mistakes corrections must align with law_says legal claims — if a mistake's "correct" answer contradicts the law_says section, flag mistakes as "error".

## Important
You MUST return a valid JSON object matching the schema. Every field is required. scores.total must equal the sum of all five score fields."""


def validate_user_content(
    title: str,
    lane: str | None,
    what_changed: str | None,
    law_says: str | None,
    do_now: str | None,
    uncertain: str | None,
    mistakes: str | None,
) -> str:
    return f"""Please validate this DPDPA blog post:

TITLE: {title}
LANE: {lane if lane is not None else "undefined"}

--- SECTION: What Changed ---
{what_changed or "(empty)"}

--- SECTION: What the Law Actually Says ---
{law_says or "(empty)"}

--- SECTION: What Businesses Should Do Now ---
{do_now or "(empty)"}

--- SECTION: What Is Still Uncertain ---
{uncertain or "(empty)"}

--- SECTION: Top Mistakes to Avoid ---
{mistakes or "(empty)"}

Return the structured validation report with all scores filled in."""


# Structured-output instruction (what the AI SDK's Output.object sends as the JSON schema).
_SCHEMA_SUFFIX = (
    "\n\nRespond with ONLY a JSON object (no markdown fences, no prose) that conforms to this JSON schema:\n"
    + json.dumps(ValidateOutput.model_json_schema(), separators=(",", ":"))
)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_validation(text: str) -> ValidateOutput | None:
    cleaned = _FENCE.sub("", (text or "").strip())
    try:
        return ValidateOutput.model_validate_json(cleaned)
    except (ValidationError, ValueError):
        return None


def run_validation(user_content: str) -> ValidateOutput | None:
    text = llm.complete(
        VALIDATE_SYSTEM_PROMPT,
        [{"role": "user", "content": user_content + _SCHEMA_SUFFIX}],
        model=MODEL,
    )
    return parse_validation(text)


def ist_date(now: datetime | None = None) -> str:
    """Validation date computed server-side in IST (never trust the model for dates)."""
    return ((now or datetime.now(UTC)) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ── Revise ─────────────────────────────────────────────────────────────────
SECTION_LABELS: dict[str, str] = {
    "section_what_changed": "What Changed",
    "section_law_says": "What the Law Actually Says",
    "section_do_now": "What Businesses Should Do Now",
    "section_uncertain": "What Is Still Uncertain",
    "section_mistakes": "Top Mistakes to Avoid",
}

REVISE_SYSTEM_PROMPT = """You are a senior DPDPA editorial corrector for SaralPrivacy.com.

You are given a single section of a blog post that has been flagged by the DPDPA Guardrail Validator, along with the specific validation feedback explaining what is wrong.

Your task is to rewrite ONLY that section to address every issue raised in the validation feedback.

## Strict Rules

1. **No speculation.** Remove any claim that is not directly traceable to the DPDPA Act 2023 text or notified DPDP Rules 2025. Do not use words like "likely", "proposed", "may be introduced", "expected", "could", unless clearly labelling them as speculation with explicit attribution.

2. **Official citations only.** Replace unofficial source references (blog posts, commentary sites, non-government URLs) with official citations: DPDPA Act 2023 (section number), DPDP Rules 2025 (rule number), or Gazette of India notifications. If a claim cannot be officially cited, remove it.

3. **Correct legal terminology.** Use the exact terms from the Act — do not import GDPR concepts, common-law tort terms, or terminology not present in the DPDPA. For example:
   - "vicarious liability" → "Data Fiduciary accountability for Data Processors (Section 8(2))"
   - "legitimate interest" → does not exist in DPDPA; use "consent or specified legitimate use"
   - "DPO" attribution must cite the specific Rule, not Section 10(1) directly

4. **Preserve structure and intent.** Keep the same heading style, paragraph structure, and bullet points as the original. Do not rewrite from scratch — correct what the validator flagged, leave the rest intact.

5. **Fix formatting artifacts.** Remove any leftover markdown artifacts (e.g., "## heading" inside a textarea that renders as plain text), "dpdpa" fragments accidentally appended to words, broken table cell text.

6. **Return ONLY the corrected section text.** No preamble, no explanation, no "Here is the corrected version:" prefix. Just the corrected content, ready to replace the textarea value directly."""


def revise_prompt(
    *,
    section_key: str,
    current_content: str,
    feedback_note: str,
    title: str | None,
    sections: dict[str, str | None],
) -> str:
    label = SECTION_LABELS.get(section_key, section_key)
    others = [
        f"--- {SECTION_LABELS.get(key, key)} ---\n{content.strip()}"
        for key, content in sections.items()
        if key != section_key and content and content.strip()
    ]
    context = (
        "\n\n## Other Sections in This Post (for cross-section consistency — do NOT rewrite these)\n"
        + "\n\n".join(others)
        if others
        else ""
    )
    return f"""Blog post title: "{title if title is not None else "undefined"}"

Section being corrected: "{label}"

--- CURRENT SECTION CONTENT ---
{current_content}

--- VALIDATION FEEDBACK (issues to fix) ---
{feedback_note}{context}

Please rewrite the section content above, addressing every issue in the validation feedback. Ensure your revision is consistent with the other sections shown above — do NOT contradict their legal claims or duplicate their content. Follow all rules in your system prompt. Return only the corrected section text."""


def run_revision(prompt: str) -> str:
    return llm.complete(REVISE_SYSTEM_PROMPT, [{"role": "user", "content": prompt}], model=MODEL)
