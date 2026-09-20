"""Row → document mapping and small text helpers shared by the editorial routes.

Documents keep the shape the pages consumed through `lib/db` (`rowToDoc`): every column
under its Appwrite-era field name, plus `id`, `$id`, `$createdAt`, `$updatedAt`. For
briefings the application `created_at` is the renamed `created_at_attr` column.
"""

import json
import math
import re
from datetime import date, datetime
from typing import Any

from app.models.editorial import BlogPost, Briefing

# Never leaves the server: it is the one-click "send to subscribers" credential.
_PRIVATE_BRIEFING_FIELDS = frozenset({"approval_token"})


def _iso(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def _base(row: Briefing | BlogPost) -> dict[str, Any]:
    data = {k: _iso(v) for k, v in row.model_dump().items()}
    data["id"] = str(row.id)
    data["$id"] = str(row.id)
    data["$createdAt"] = _iso(row.created_at)
    data["$updatedAt"] = _iso(row.updated_at)
    return data


def briefing_doc(row: Briefing, *, include_private: bool = False) -> dict[str, Any]:
    data = _base(row)
    data["created_at"] = data.pop("created_at_attr", None)
    if not include_private:
        for key in _PRIVATE_BRIEFING_FIELDS:
            data.pop(key, None)
    return data


def blog_doc(row: BlogPost) -> dict[str, Any]:
    return _base(row)


# ── JS-compatible text helpers ────────────────────────────────────────────
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_title(title: str) -> str:
    """`title.toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"").slice(0,60)`."""
    s = _NON_ALNUM.sub("-", (title or "").lower())
    s = re.sub(r"^-|-$", "", s)
    return s[:60]


def briefing_slug(date_str: str, title: str) -> str:
    return f"{date_str}-{slugify_title(title)}"


def js_round(x: float) -> int:
    """Math.round: halves round up (Python's round() is banker's rounding)."""
    return math.floor(x + 0.5)


def word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def first_truthy(*values: Any, default: Any = "") -> Any:
    """`a || b || c || default` with JavaScript truthiness."""
    for v in values:
        if v:
            return v
    return default


def parse_json(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str):
        return raw if raw is not None else fallback
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return fallback


def js_json(value: Any) -> str:
    """JSON.stringify: compact separators, non-ASCII kept as-is."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
