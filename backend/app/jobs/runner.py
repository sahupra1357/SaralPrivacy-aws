"""Runs one job under a Postgres advisory lock so two workers never double-run it."""

import logging
import zlib

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine
from app.jobs.registry import JobResult, JobSpec

log = logging.getLogger(__name__)


def _lock_key(name: str) -> int:
    return zlib.crc32(name.encode()) & 0x7FFFFFFF


def run_job(spec: JobSpec) -> JobResult | None:
    """Returns None when another worker holds the lock (skipped)."""
    with Session(engine) as session:
        got = session.execute(
            text("select pg_try_advisory_lock(:k)"), {"k": _lock_key(spec.name)}
        ).scalar()
        if not got:
            log.info("job %s skipped: lock held elsewhere", spec.name)
            return None
        try:
            log.info("job %s start", spec.name)
            result = spec.fn(session)
            log.info("job %s done ok=%s %s", spec.name, result.ok, result.summary)
            return result
        except Exception:
            log.exception("job %s crashed", spec.name)
            return JobResult(ok=False, summary="crashed")
        finally:
            session.execute(text("select pg_advisory_unlock(:k)"), {"k": _lock_key(spec.name)})
            session.commit()
