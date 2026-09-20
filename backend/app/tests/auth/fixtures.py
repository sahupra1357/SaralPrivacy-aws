"""Seeded users and bearer headers, shared with every other module's tests.

Registered as a pytest plugin from the root conftest:

    pytest_plugins = ["app.tests.auth.fixtures"]

(a `pytest_plugins` line is only allowed in the *root* conftest, which the orchestrator
owns — `app/tests/auth/conftest.py` re-exports these so the auth tests work either way).

`admin_headers` / `blogger_headers` are real tokens for real rows with live `app.sessions`
entries, so `deps.CurrentUser` and `require_role(...)` behave exactly as in production.
"""

from collections.abc import Callable

import pytest
from sqlmodel import Session

from app.core import security
from app.crud import auth as crud
from app.models.auth import User

ADMIN_EMAIL = "admin@test.local"
BLOGGER_EMAIL = "blogger@test.local"
TEST_PASSWORD = "correct-horse-battery"  # 21 chars: inside the 12–128 rule


def make_user(
    session: Session,
    *,
    email: str,
    role: str = "admin",
    password: str = TEST_PASSWORD,
    display_name: str | None = None,
    is_active: bool = True,
    totp_confirmed: bool = True,
) -> User:
    """A user ready to sign in. `totp_confirmed` also stores a usable TOTP secret."""
    user = crud.create_user(
        session,
        email=email,
        role=role,
        password=password,
        display_name=display_name or role.title(),
        is_active=is_active,
    )
    if totp_confirmed:
        crud.store_totp_secret(session, user, security.new_totp_secret())
        user.totp_confirmed = True
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def bearer(session: Session, user: User) -> dict[str, str]:
    """A live session row plus the matching access token, as `/auth/mfa/verify` mints it."""
    row = crud.create_session(session, user, ip="127.0.0.1", user_agent="pytest")
    token = security.create_access_token(user.id, user.role, row.id, name=user.display_name)  # type: ignore[arg-type]
    return {"Authorization": f"Bearer {token}"}


def pending_bearer(user: User) -> dict[str, str]:
    """The 10-minute token `/auth/login` hands back, as a bearer header."""
    return {"Authorization": f"Bearer {security.create_pending_token(user.id)}"}


@pytest.fixture
def make_test_user(session: Session) -> Callable[..., User]:
    def _make(**kwargs: object) -> User:
        return make_user(session, **kwargs)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def admin_user(session: Session) -> User:
    return make_user(session, email=ADMIN_EMAIL, role="admin", display_name="Admin")


@pytest.fixture
def blogger_user(session: Session) -> User:
    return make_user(session, email=BLOGGER_EMAIL, role="blogger", display_name="Desk")


@pytest.fixture
def admin_headers(session: Session, admin_user: User) -> dict[str, str]:
    return bearer(session, admin_user)


@pytest.fixture
def blogger_headers(session: Session, blogger_user: User) -> dict[str, str]:
    return bearer(session, blogger_user)
