"""Admin module jobs.

- `aeo-panel`   "30 3 * * 1"  — the weekly AEO Citation Panel (was the Vercel cron
  /api/cron/aeo-panel, Monday 03:30 UTC ≈ 09:00 IST).
- `seo-inspect` "0 4 * * 1"   — the weekly Search Console watcher, scope `full`, no sitemap
  resubmit (was .github/workflows/seo-inspect.yml, Monday 04:00 UTC).

Manual run: `python -m app.jobs run aeo-panel` / `python -m app.jobs run seo-inspect`.
"""

import logging

from sqlmodel import Session

from app.api.routes import aeo, seo
from app.core.config import settings
from app.jobs.registry import JobResult

log = logging.getLogger(__name__)

AEO_PANEL_CRON = "30 3 * * 1"
SEO_INSPECT_CRON = "0 4 * * 1"


def run_aeo_panel(session: Session) -> JobResult:
    api_key = settings.OPENROUTER_API_KEY
    if not api_key:
        return JobResult(ok=False, summary=aeo.MISSING_KEY_CRON)
    ok, payload = aeo.execute_panel(session, api_key)
    if not ok:
        return JobResult(ok=False, summary=str(payload.get("error")), details=payload)
    s = payload["summary"]
    return JobResult(
        ok=True,
        summary=(
            f"{s['cited']}/{s['cleanTotal']} clean prompts cited · {s['errored']} errored · "
            f"persisted {payload['persisted']}"
        ),
        details=payload,
    )


def run_seo_inspect(session: Session) -> JobResult:
    try:
        loaded = seo.load_service_account()
    except ValueError as exc:
        return JobResult(ok=False, summary=str(exc))
    if loaded is None:
        return JobResult(ok=False, summary="GSC_SERVICE_ACCOUNT_JSON is not configured")
    sa, key_source = loaded
    ok, payload = seo.execute_inspection(
        session, sa=sa, key_source=key_source, scope="full", submit_sitemap=False
    )
    for line in payload.get("log", []):
        log.info("seo-inspect: %s", line)
    if not ok:
        return JobResult(ok=False, summary=str(payload.get("error")), details=payload)
    return JobResult(
        ok=True,
        summary=f"{payload['verdict']} — {payload['summary']}",
        details=payload,
    )
