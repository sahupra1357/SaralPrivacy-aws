"""Publish the generated briefing (rewritten from tools/publish_to_webapp.py).

The old tool uploaded the image to Supabase Storage and POSTed the payload to the site's
own `/api/briefings/generate`; here both happen in-process: `storage.put` and
`create_manual_briefing` (the same code path the generate route runs). Then the
`briefings` ISR tag is busted and the new detail page pre-warmed.
"""

import json
import logging
from typing import Any

import httpx
from sqlmodel import Session

from app.editorial import config
from app.editorial.briefings import create_manual_briefing
from app.editorial.taxonomy import derive
from app.models.editorial import Briefing
from app.services import revalidate, storage

log = logging.getLogger(__name__)


def upload_infographic(date_str: str, image: bytes) -> str:
    """Deterministic key per day (`inf20260328.jpg`) so a retry overwrites cleanly."""
    key = f"infographics/inf{date_str.replace('-', '')}.jpg"
    try:
        url = storage.put(key, image, "image/jpeg")
        log.info("Infographic uploaded → %s", url)
        return url
    except Exception as e:  # noqa: BLE001 — publish without an image rather than fail
        log.warning("Infographic upload failed — briefing will publish without infographic: %s", e)
        return ""


def build_payload(content: dict[str, Any], infographic_url: str) -> dict[str, Any]:
    """The /briefings/generate payload. Every Hook/Body/CTA field rides in the
    `why_it_matters` JSON envelope that the detail page's parseWhyField() unpacks.
    Facets: Stage → category, Sector → industries[0], Format → tags[0]."""
    date_str = content.get("date", "")
    day_num = content.get("day_number", 1)
    tax = derive(content)
    overview = content.get("overview", {})
    key_points = content.get("key_points", {})
    what_this_means = content.get("what_this_means", {})
    action_items = content.get("action_items", {})
    summary = overview.get("body", "")

    why_rich = json.dumps(
        {
            "why": overview.get("body", ""),
            "why_heading": overview.get("heading", ""),
            "impact": what_this_means.get("body", ""),
            "body_heading": what_this_means.get(
                "heading", "What does this mean for YOUR business?"
            ),
            "affected": key_points.get("points", []),
            "key_heading": key_points.get("heading", ""),
            "save": content.get("save_worthy_takeaway", ""),
            "inf_title": content.get("infographic", {}).get("title", ""),
            "stage": tax["stage"],
            "sector": tax["sector"],
            "content_type": tax["content_type"],
            "week_theme": content.get("week_theme", ""),
        },
        ensure_ascii=False,
    )
    checklist = [i.get("action", "") for i in action_items.get("items", []) if i.get("action")]
    topic = str(content.get("topic", ""))
    word_tags = [w.lower() for w in topic.replace(",", "").split()[:5]]
    tags = [tax["content_type"]] + [w for w in word_tags if w != tax["content_type"]]

    return {
        "title": content.get("subject_line", f"Day {day_num}: {topic}"),
        "excerpt": content.get("preview_text", summary[:200]),
        "summary": summary,
        "why_it_matters": why_rich,
        "action_checklist": checklist,
        "category": tax["stage"],
        "tags": tags,
        "industries": [tax["sector"]],
        "read_time": max(3, int(content.get("word_count", 300)) // 200),
        "featured": False,
        "author": "DPDPA Editorial Team",
        "infographic_url": infographic_url,
        "day_number": day_num,
        "topic": topic,
        "date": date_str,
        "week_theme": content.get("week_theme", ""),
    }


def prewarm(slug: str) -> None:
    """Render + cache the new detail page so the first visitor is not the cold render."""
    if not slug:
        return
    try:
        r = httpx.get(f"{config.site_url()}/briefings/{slug}", timeout=30)
        if r.status_code >= 400:
            log.warning("ISR pre-warm returned %s for /briefings/%s", r.status_code, slug)
    except httpx.HTTPError as e:
        log.warning("ISR pre-warm failed (non-blocking): %s", e)


def publish(session: Session, content: dict[str, Any], image: bytes | None) -> Briefing:
    date_str = str(content.get("date", ""))
    url = upload_infographic(date_str, image) if image else ""
    briefing = create_manual_briefing(session, build_payload(content, url))
    if not revalidate.tag("briefings"):
        log.warning("Cache revalidation failed — page will refresh within 1 hour.")
    prewarm(briefing.slug)
    return briefing
