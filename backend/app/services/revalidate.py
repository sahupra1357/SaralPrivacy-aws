"""Ask the Next.js frontend to bust an ISR tag/path after a write (blog publish etc.).

Calls the frontend's /api/revalidate with CRON_SECRET, same contract as today.
Failures are logged, never raised: a stale page is better than a failed publish.
"""

import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)


def tag(name: str) -> bool:
    return _call({"tag": name})


def path(p: str) -> bool:
    return _call({"path": p})


def _call(body: dict[str, str]) -> bool:
    url = f"{settings.FRONTEND_HOST.rstrip('/')}/api/revalidate"
    try:
        r = httpx.post(
            url, json=body, headers={"Authorization": f"Bearer {settings.CRON_SECRET}"}, timeout=10
        )
        ok = r.status_code < 300
        if not ok:
            log.warning("revalidate %s -> %s %s", body, r.status_code, r.text[:200])
        return ok
    except httpx.HTTPError as e:
        log.warning("revalidate %s failed: %s", body, e)
        return False
