"""Scheduled jobs. Modules define `run_<name>(session)` in app/jobs/<module>.py and the
orchestrator registers them here. `python -m app.jobs` runs the scheduler (worker container).
"""

from app.jobs.registry import JobSpec, register, registry

__all__ = ["JobSpec", "register", "registry"]

# ── Registrations (orchestrator only; one line per job) ──────────────────────
from app.jobs import outreach as _outreach  # noqa: E402

register("outreach-send", "0 4 * * *", _outreach.run_outreach_send)
register("briefing-send", "30 4 * * *", _outreach.run_briefing_send, timeout_seconds=600)
from app.jobs import admin as _admin  # noqa: E402

register("aeo-panel", _admin.AEO_PANEL_CRON, _admin.run_aeo_panel)
register("seo-inspect", _admin.SEO_INSPECT_CRON, _admin.run_seo_inspect, timeout_seconds=900)
from app.jobs.editorial import run_daily_briefing  # noqa: E402

register("editorial_daily_briefing", "30 3 * * *", run_daily_briefing, timeout_seconds=900)
