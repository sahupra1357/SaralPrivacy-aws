"""Fixtures for the admin module's tests.

The auth fixtures (`admin_headers`, `blogger_headers`, …) are re-imported here, as
app/tests/auth/conftest.py does, so this directory works with or without the root
`pytest_plugins` line.
"""

from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.tests.auth.fixtures import (  # noqa: F401
    admin_headers,
    admin_user,
    blogger_headers,
    blogger_user,
    make_test_user,
)


@pytest.fixture(autouse=True)
def task_session_is_test_session(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    """Background runs open their own session in production; in tests they must use the
    rolled-back test session so nothing leaks between tests."""
    from app.api.routes import admin as admin_routes

    monkeypatch.setattr(admin_routes, "task_session", lambda: nullcontext(session))


@pytest.fixture
def insert_row(session: Session) -> Callable[..., dict[str, Any]]:
    """Insert into a table another module owns without importing its model."""

    def _insert(table: str, **values: Any) -> dict[str, Any]:
        cols = ", ".join(f'"{k}"' for k in values)
        params = ", ".join(f":{k}" for k in values)
        row = (
            session.execute(
                text(f"insert into {table} ({cols}) values ({params}) returning *"), values
            )
            .mappings()
            .one()
        )
        session.commit()
        return dict(row)

    return _insert
