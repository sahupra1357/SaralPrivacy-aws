"""Newsletter content via Claude, strictly validated (rewritten from tools/generate_content.py)."""

import json
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.editorial import config
from app.services import llm

log = logging.getLogger(__name__)

MAX_RETRIES = 2  # 3 attempts in total
RETRY_DELAY_SECONDS = 5

_sleep = time.sleep  # patched in tests


class ActionItem(BaseModel):
    priority: str
    action: str
    owner: str


class ContentSection(BaseModel):
    heading: str
    body: str


class KeyPoints(BaseModel):
    heading: str
    points: list[str]


class ActionItems(BaseModel):
    heading: str
    items: list[ActionItem]


class InfographicData(BaseModel):
    type: str
    title: str
    description: str
    data_points: list[str]


class NewsletterContent(BaseModel):
    topic: str
    date: str
    day_number: int
    subject_line: str
    preview_text: str
    overview: ContentSection
    key_points: KeyPoints
    what_this_means: ContentSection
    action_items: ActionItems
    save_worthy_takeaway: str
    infographic: InfographicData
    sources_used: list[str]
    word_count: int


SYSTEM_PROMPT = """You are the writer of DPDPA Daily Brief by SaralPrivacy — India's simplest newsletter on data privacy for small businesses.

YOUR READER: A small business owner in India. Class 8 education. Runs a shop, factory, school, or small office. Does not know legal English. Uses WhatsApp every day.

YOUR WRITING RULES (follow strictly):
1. Write like you are talking to a friend. Short sentences. Max 15 words per sentence.
2. Use the simplest word possible. NOT "personal data" — say "people's information". NOT "data fiduciary" — say "your business". NOT "pursuant to" — say "because of".
3. No English words that a Class 8 student would not know.
4. Every section must answer: "So what? Why should I care TODAY?"
5. Use Indian examples: aadhaar, pan card, customer phone numbers, WhatsApp messages, salary slips, resumes.
6. STRUCTURE every briefing as: HOOK → BODY → CTA. See field descriptions below.

Output ONLY valid JSON. No markdown. No code fences. No preamble. No explanation outside the JSON object."""


def build_user_prompt(research: dict[str, Any]) -> str:
    td = research.get("topic_data", {})
    topic = td.get("topic", "")
    concept = td.get("concept", "")
    clarification = td.get("clarification", "")
    takeaway = td.get("save_worthy_takeaway", "")
    date_str = td.get("date", "")
    day_num = td.get("day", 1)
    week_theme = td.get("week_theme", "")
    infographic_type = td.get("infographic_type", "stat")
    raw_snippets = research.get("raw_snippets", "")
    sources = research.get("sources", [])

    if raw_snippets and not research.get("knowledge_only", False):
        context_block = f"""
## Web Research (use to enrich content — do not copy verbatim)
{raw_snippets[:3000]}
"""
    else:
        context_block = (
            "## Note: No web research available. Use your knowledge of Indian DPDPA context."
        )

    source_urls = [s.get("url", "") for s in sources[:5] if s.get("url")]

    return f"""Write the Day {day_num} DPDPA Daily Brief for SaralPrivacy.

## Topic
{topic}

## Week Theme
{week_theme}

## Core Concept (must be communicated clearly)
{concept}

## Clarification (expand on this)
{clarification}

## Save-Worthy Takeaway (copy this VERBATIM into save_worthy_takeaway)
{takeaway}

## Date
{date_str}
{context_block}

## STRUCTURE: Hook → Body → CTA
The briefing must follow this 3-part structure:

HOOK (overview field): Start with a surprising fact, a relatable fear, or a short story. Make the reader say "Oh no, does this apply to me?" Max 3 sentences. Simple words only.

BODY (key_points + what_this_means): Explain what is happening and why it matters. Use bullet points. Give 3-4 real examples from Indian small business life. Max 12 words per sentence.

CTA (action_items): End with clear steps. Last item MUST be: "Check if your business is ready. It takes 3 minutes. Go to saralprivacy.com/assessment"

## Output Schema (strict JSON, all fields required)
{{
  "topic": "{topic}",
  "date": "{date_str}",
  "day_number": {day_num},
  "subject_line": "[Max 55 chars. Punchy. Simple words. E.g. 'Are you breaking this new law without knowing?']",
  "preview_text": "[One sentence. Max 90 chars. Conversational.]",
  "overview": {{
    "heading": "[Short heading — question or bold statement, max 8 words]",
    "body": "[HOOK: 60-80 words. Start with a surprising fact or fear. Simple Class 8 language. Make reader say 'this applies to me'.]"
  }},
  "key_points": {{
    "heading": "[e.g. '3 things every small business must know']",
    "points": ["[point 1 — max 15 words, simple language]", "[point 2]", "[point 3]", "[point 4 — optional]"]
  }},
  "what_this_means": {{
    "heading": "What does this mean for YOUR business?",
    "body": "[BODY: 80-100 words. Practical. Indian examples. WhatsApp, Aadhaar, phone numbers, resumes. Max 12 words per sentence.]"
  }},
  "action_items": {{
    "heading": "3 things you can do this week",
    "items": [
      {{"priority": "high", "action": "[Simple action — max 12 words]", "owner": "Founder"}},
      {{"priority": "medium", "action": "[Simple action]", "owner": "HR or Ops"}},
      {{"priority": "low", "action": "Check if your business is ready. Takes 3 minutes. Visit saralprivacy.com/assessment", "owner": "Founder"}}
    ]
  }},
  "save_worthy_takeaway": "{takeaway}",
  "infographic": {{
    "type": "{infographic_type}",
    "title": "[Short title — max 6 words, simple]",
    "description": "[What the infographic shows. 1-2 sentences. Designed for a Class 8 reader.]",
    "data_points": ["[Concrete fact or step — simple words]", "[fact 2]", "[fact 3]", "[fact 4]"]
  }},
  "sources_used": {json.dumps(source_urls)},
  "word_count": 0
}}

Rules:
- Every sentence must be Class 8 level. If in doubt — simplify.
- Use Indian examples: aadhaar, pan card, WhatsApp, salary slip, resume, GST, shop, factory.
- action_items last item must always link to saralprivacy.com/assessment
- infographic data_points: concrete numbers, steps, or short facts — never vague
- word_count: count total words across overview.body + key_points.points + what_this_means.body
- Return ONLY the JSON object. Nothing else."""


def parse_and_validate(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw: {text[:500]}") from e
    try:
        result = NewsletterContent(**data).model_dump()
    except (ValidationError, TypeError) as e:
        raise ValueError(f"Content schema validation failed: {e}") from e
    if result.get("word_count", 0) == 0:
        # Same concatenation as the Python tool (no separators between the three parts).
        words = (
            result["overview"]["body"]
            + " ".join(result["key_points"]["points"])
            + result["what_this_means"]["body"]
        )
        result["word_count"] = len(words.split())
    return result


def call_claude(prompt: str) -> str:
    return llm.complete(
        SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
        model=config.pipeline_model(),
        max_tokens=config.pipeline_max_tokens(),
        temperature=config.pipeline_temperature(),
    )


def generate_content(research: dict[str, Any]) -> dict[str, Any]:
    prompt = build_user_prompt(research)
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            log.info("Calling Claude API (attempt %s/%s)", attempt, MAX_RETRIES + 1)
            result = parse_and_validate(call_claude(prompt))
            log.info("Content generated: %r (%s words)", result["topic"], result["word_count"])
            return result
        except Exception as e:  # noqa: BLE001 — retried, then surfaced below
            last_error = e
            log.warning("Attempt %s failed: %s", attempt, e)
            if attempt <= MAX_RETRIES:
                _sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(
        f"Content generation failed after {MAX_RETRIES + 1} attempts. Last error: {last_error}"
    )
