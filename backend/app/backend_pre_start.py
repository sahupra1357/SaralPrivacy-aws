import logging

from sqlalchemy import text
from sqlmodel import Session
from tenacity import after_log, before_log, retry, stop_after_attempt, wait_fixed

from app.core.db import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(60),
    wait=wait_fixed(1),
    before=before_log(logger, logging.INFO),
    after=after_log(logger, logging.WARN),
)
def init() -> None:
    with Session(engine) as session:
        session.execute(text("select 1"))


def ensure_schemas() -> None:
    """Create the schemas Alembic needs before its first run. Its version table lives in
    `app`, so on an empty managed database (Render, RDS) `alembic upgrade head` would fail
    without this. Idempotent; mirrors db/init/01_schemas.sql."""
    with engine.begin() as conn:
        conn.execute(text("create schema if not exists ops"))
        conn.execute(text("create schema if not exists app"))
        conn.execute(text("create extension if not exists pgcrypto"))


if __name__ == "__main__":
    logger.info("waiting for database")
    init()
    ensure_schemas()
    logger.info("database ready")
