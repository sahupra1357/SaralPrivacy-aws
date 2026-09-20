"""Editorial settings.

`app/core/config.py` is owned by core, so the keys this module adds are read with a
default until the orchestrator adds them to `Settings` (listed in
docs/build/status/editorial.md). Nothing else in the module touches `settings` for them.
"""

from pathlib import Path

from app.core.config import settings


def _str(name: str, default: str = "") -> str:
    value = getattr(settings, name, None)
    return str(value).strip() if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def site_url() -> str:
    return (settings.NEXT_PUBLIC_SITE_URL or "https://saralprivacy.com").rstrip("/")


def cron_secret() -> str:
    """`CRON_SECRET || BRIEFING_CRON_SECRET`, as generate/today/revalidate compared."""
    return (settings.CRON_SECRET or "").strip() or _str("BRIEFING_CRON_SECRET")


def briefing_cron_secret() -> str:
    """The delete route compared BRIEFING_CRON_SECRET only; CRON_SECRET is the fallback now
    that the pipeline runs in-process and the old secret may be retired."""
    return _str("BRIEFING_CRON_SECRET") or (settings.CRON_SECRET or "").strip()


def google_sheet_id() -> str:
    return _str("GOOGLE_SHEET_ID")


def google_credentials_json() -> str:
    """Full service-account key JSON (was the GOOGLE_CREDENTIALS_JSON GH secret)."""
    return _str("GOOGLE_CREDENTIALS_JSON")


def google_credentials_path() -> str:
    return _str("GOOGLE_CREDENTIALS_PATH")


# Bundled copy of the roadmap (was roadmap/90_day_roadmap.csv at the repo root).
_BUNDLED_ROADMAP = Path(__file__).resolve().parent / "data" / "roadmap" / "90_day_roadmap.csv"


def roadmap_csv_path() -> str:
    """Local CSV fallback used when Google Sheets is unavailable. ROADMAP_CSV_PATH
    overrides; otherwise the CSV shipped inside the backend image."""
    return _str("ROADMAP_CSV_PATH") or str(_BUNDLED_ROADMAP)


def serp_api_key() -> str:
    return _str("SERP_API_KEY")


def kie_api_key() -> str:
    return _str("KIE_API_KEY")


def nano_banana_model() -> str:
    return _str("NANO_BANANA_MODEL", "nano-banana-2")


def pipeline_model() -> str:
    return _str("CLAUDE_MODEL", "claude-sonnet-4-6")


def pipeline_max_tokens() -> int:
    return _int("CLAUDE_MAX_TOKENS", 4096)


def pipeline_temperature() -> float:
    return _float("CLAUDE_TEMPERATURE", 0.3)


def extra_admin_emails() -> list[str]:
    raw = settings.EXTRA_ADMIN_EMAILS or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def email_link_secret() -> str:
    return (settings.EMAIL_LINK_SECRET or "").strip()
