"""Derive the three discovery facets for a briefing (stage / sector / format).

Rewritten from tools/briefing_taxonomy.py; mirror of
frontend/lib/data/briefing-taxonomy.ts (keep slugs in sync). Slugs ride on existing
columns: Stage -> category, Sector -> industries[0], Format -> tags[0]. Each accepts an
explicit roadmap column when present, else derives deterministically.
"""

from collections.abc import Mapping
from typing import Any, TypedDict

# Day 91–240 industry blocks and the day 1–90 foundational arc.
_STAGE_BY_WEEK_THEME: dict[str, str] = {
    "industry data reality": "assess",
    "industry compliance touchpoints": "fix",
    "industry response packs": "sustain",
    "what is dpdpa?": "learn",
    "why smbs should care": "learn",
    "business implications": "learn",
    "where to start": "assess",
    "how dpdpa affects each function": "assess",
    "what readiness looks like": "assess",
    "industry use cases": "assess",
    "mistakes smbs make": "fix",
    "30-day smb action plan": "fix",
    "act now": "fix",
    "make it practical": "fix",
    "convert awareness into commitment": "sustain",
}
STAGE_SLUGS = frozenset({"learn", "assess", "fix", "sustain"})

# Keyed by the lowercase topic prefix used in the roadmap (text before the first ":").
_SECTOR_SLUG_BY_PREFIX: dict[str, str] = {
    "ca firms": "ca-firms",
    "recruitment firms": "recruitment",
    "training institutes": "training-institutes",
    "d2c brands": "d2c-brands",
    "clinics and labs": "clinics-labs",
    "schools and colleges": "schools-colleges",
    "law firms": "law-firms",
    "real estate firms": "real-estate",
    "hospitality businesses": "hospitality",
    "pharmacies": "pharmacies",
    "nbfcs": "nbfc",
    "insurance brokers": "insurance-brokers",
    "manufacturing firms": "manufacturing",
    "logistics firms": "logistics",
    "retail chains": "retail",
    "restaurants and cloud kitchens": "restaurants",
    "fitness and wellness": "fitness-wellness",
    "it services and saas startups": "it-saas",
    "marketing agencies": "marketing-agencies",
    "consultancies and bpos": "consultancies-bpo",
    "hospitals and healthcare groups": "hospitals",
    "edtech and online learning": "edtech",
    "marketplaces and platform businesses": "marketplaces",
    "housing societies and proptech": "housing-proptech",
    "auto dealers and service centres": "auto-dealers",
    "ngos and member networks": "ngos",
    "media and community platforms": "media-community",
    "travel tech and booking platforms": "travel-tech",
    "fintech and payments-adjacent smes": "fintech",
    "multi-location service businesses": "multi-location",
}

_FORMAT_BY_INFOGRAPHIC: dict[str, str] = {
    "stat": "stat",
    "process": "explainer",
    "checklist": "checklist",
    "timeline": "playbook",
    "comparison": "explainer",
}


class Facets(TypedDict):
    stage: str
    sector: str
    content_type: str


def stage_for(week_theme: str = "", explicit: str = "") -> str:
    e = (explicit or "").strip().lower()
    if e in STAGE_SLUGS:
        return e
    return _STAGE_BY_WEEK_THEME.get((week_theme or "").strip().lower(), "learn")


def sector_for(topic: str = "", explicit: str = "") -> str:
    e = (explicit or "").strip().lower()
    if e:
        return e
    t = topic or ""
    if ":" in t:
        prefix = t.split(":")[0].strip().lower()
        if prefix in _SECTOR_SLUG_BY_PREFIX:
            return _SECTOR_SLUG_BY_PREFIX[prefix]
    return "general"


def content_type_for(infographic_type: str = "", stage: str = "", explicit: str = "") -> str:
    e = (explicit or "").strip().lower()
    if e:
        return e
    ig = (infographic_type or "").strip().lower()
    # Response-pack process/timeline pieces are playbooks, not generic explainers.
    if stage == "sustain" and ig in ("process", "timeline"):
        return "playbook"
    return _FORMAT_BY_INFOGRAPHIC.get(ig, "explainer")


def derive(row: Mapping[str, Any]) -> Facets:
    def get(key: str) -> str:
        value = row.get(key, "")
        return "" if value is None else str(value)

    stage = stage_for(get("week_theme"), get("stage"))
    return {
        "stage": stage,
        "sector": sector_for(get("topic"), get("sector")),
        "content_type": content_type_for(get("infographic_type"), stage, get("content_type")),
    }
