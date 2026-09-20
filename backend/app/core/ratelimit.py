"""Postgres-backed fixed-window rate limiter shared by every container.

Replaces the per-instance in-memory Map from `abuseGuard.ts`. Keys look like
"contact:<ip>" or "mfa:<user id>". One row per key in `app.login_attempts`; the row
is upserted atomically so concurrent requests count correctly.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlmodel import Session


@dataclass(frozen=True)
class RateLimitResult:
    ok: bool
    retry_after: int  # seconds; 0 when ok


_UPSERT = text(
    """
    insert into app.login_attempts (key, window_start, count)
    values (:key, :now, 1)
    on conflict (key) do update set
        count = case when app.login_attempts.window_start < :window_floor then 1
                     else app.login_attempts.count + 1 end,
        window_start = case when app.login_attempts.window_start < :window_floor then :now
                            else app.login_attempts.window_start end
    returning count, window_start
    """
)


def hit(session: Session, key: str, limit: int, window_seconds: int) -> RateLimitResult:
    now = datetime.now(UTC)
    row = session.execute(
        _UPSERT,
        {"key": key, "now": now, "window_floor": now - timedelta(seconds=window_seconds)},
    ).one()
    session.commit()
    count, window_start = int(row[0]), row[1]
    if count <= limit:
        return RateLimitResult(ok=True, retry_after=0)
    if window_start.tzinfo is None:
        window_start = window_start.replace(tzinfo=UTC)
    remaining = (window_start + timedelta(seconds=window_seconds) - now).total_seconds()
    return RateLimitResult(ok=False, retry_after=max(1, int(remaining) + 1))
