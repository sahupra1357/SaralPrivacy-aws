"""crud.auth + the TOTP helpers in core.security, exercised directly."""

import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.core import security
from app.core.config import settings
from app.crud import auth as crud
from app.models.auth import AuthSession, User
from app.tests.auth.fixtures import make_user


# ── users ─────────────────────────────────────────────────────────────────
def test_create_user_lower_cases_the_email_and_hashes_the_password(session: Session) -> None:
    user = crud.create_user(session, email="  MiXeD@Test.Local ", role="admin", password="a" * 12)

    assert user.email == "mixed@test.local"
    assert user.hashed_password != "a" * 12
    assert security.verify_password("a" * 12, user.hashed_password)


def test_a_user_created_without_a_password_cannot_be_signed_into(session: Session) -> None:
    user = crud.create_user(session, email="nopw@test.local", role="blogger")

    assert user.hashed_password
    assert not security.verify_password("", user.hashed_password)


def test_get_user_by_email_is_case_insensitive(session: Session) -> None:
    make_user(session, email="case@test.local", role="admin")

    assert crud.get_user_by_email(session, "CASE@TEST.LOCAL") is not None


def test_store_totp_secret_encrypts_at_rest_and_round_trips(session: Session) -> None:
    user = make_user(session, email="enc@test.local", role="admin", totp_confirmed=False)
    secret = security.new_totp_secret()

    crud.store_totp_secret(session, user, secret)

    assert user.totp_secret_enc is not None
    assert secret not in user.totp_secret_enc
    assert crud.get_totp_secret(user) == secret
    assert user.totp_confirmed is False


def test_reset_totp_clears_every_totp_column(session: Session) -> None:
    user = make_user(session, email="reset@test.local", role="admin", totp_confirmed=True)
    crud.confirm_totp(session, user, 42)

    crud.reset_totp(session, user)

    assert (user.totp_secret_enc, user.totp_confirmed, user.last_totp_step) == (None, False, None)


# ── sessions ──────────────────────────────────────────────────────────────
def test_create_session_expires_after_the_configured_window(session: Session) -> None:
    user = make_user(session, email="sess@test.local", role="admin")

    row = crud.create_session(session, user, ip="1.2.3.4", user_agent="pytest")

    expected = row.issued_at + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    assert abs((row.expires_at - expected).total_seconds()) < 2
    assert crud.get_live_session(session, row.id) is not None


def test_get_live_session_ignores_revoked_and_expired_rows(session: Session) -> None:
    user = make_user(session, email="sess2@test.local", role="admin")
    revoked = crud.create_session(session, user)
    crud.revoke_session(session, revoked.id)
    expired = crud.create_session(session, user)
    session.execute(
        text("update app.sessions set expires_at = now() - interval '1 minute' where id = :i"),
        {"i": str(expired.id)},
    )
    session.commit()

    assert crud.get_live_session(session, revoked.id) is None
    assert crud.get_live_session(session, expired.id) is None
    assert crud.get_live_session(session, uuid.uuid4()) is None


def test_revoke_all_sessions_returns_the_number_it_closed(session: Session) -> None:
    user = make_user(session, email="sess3@test.local", role="admin")
    crud.create_session(session, user)
    crud.create_session(session, user)

    assert crud.revoke_all_sessions(session, user.id) == 2
    assert crud.revoke_all_sessions(session, user.id) == 0
    rows = session.exec(select(AuthSession).where(AuthSession.user_id == user.id)).all()
    assert all(r.revoked_at is not None for r in rows)


# ── invite tokens ─────────────────────────────────────────────────────────
def test_only_the_hash_of_an_invite_token_is_stored(session: Session) -> None:
    user = make_user(session, email="inv@test.local", role="admin")

    token, row = crud.create_invite_token(session, user.id, "invite", 24)

    assert row.token_hash != token
    assert row.token_hash == security.hash_opaque_token(token)
    assert crud.find_usable_invite_token(session, token, "invite") is not None
    assert crud.find_usable_invite_token(session, token, "recovery") is None
    assert crud.find_usable_invite_token(session, "made-up", "invite") is None


def test_a_used_invite_token_is_never_usable_again(session: Session) -> None:
    user = make_user(session, email="inv2@test.local", role="admin")
    token, row = crud.create_invite_token(session, user.id, "recovery", 1)

    crud.mark_invite_token_used(session, row)

    assert crud.find_usable_invite_token(session, token, "recovery") is None


# ── blogger directory ─────────────────────────────────────────────────────
def test_blogger_is_active_reads_the_ops_directory_row(session: Session) -> None:
    session.execute(
        text("insert into ops.blogger_accounts (email, name, active) values (:e, 'Desk', false)"),
        {"e": "dir@test.local"},
    )
    session.commit()

    assert crud.blogger_is_active(session, "dir@test.local") is False
    assert crud.blogger_is_active(session, "missing@test.local") is False

    crud.activate_blogger(session, "DIR@Test.local")

    assert crud.blogger_is_active(session, "dir@test.local") is True


# ── first admin ───────────────────────────────────────────────────────────
def test_ensure_first_admin_seeds_once_and_is_idempotent(session: Session) -> None:
    first = crud.ensure_first_admin(session)
    second = crud.ensure_first_admin(session)

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert first.role == "admin"
    assert first.email == settings.FIRST_ADMIN_EMAIL.strip().lower()
    admins = session.exec(select(User).where(User.role == "admin")).all()
    assert len(admins) == 1


def test_ensure_first_admin_does_nothing_when_an_admin_exists(session: Session) -> None:
    existing = make_user(session, email="already@test.local", role="admin")

    assert crud.ensure_first_admin(session) is not None
    assert session.exec(select(User).where(User.role == "admin")).all() == [existing]


# ── TOTP helpers ──────────────────────────────────────────────────────────
def test_verify_totp_accepts_the_current_code() -> None:
    secret = security.new_totp_secret()
    code = pyotp.TOTP(secret, interval=security.TOTP_INTERVAL).now()

    # The step may tick over between generating and verifying; both are the "current" code.
    assert security.verify_totp(secret, code) in (security.totp_step(), security.totp_step() - 1)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", "12345a"])
def test_verify_totp_rejects_anything_that_is_not_six_digits(bad: str) -> None:
    assert security.verify_totp(security.new_totp_secret(), bad) is None


def test_verify_totp_refuses_a_step_already_used() -> None:
    secret = security.new_totp_secret()
    step = security.totp_step()
    code = pyotp.TOTP(secret, interval=security.TOTP_INTERVAL).at(step * security.TOTP_INTERVAL)

    assert security.verify_totp(secret, code, last_step=step) is None
    assert security.verify_totp(secret, code, last_step=step - 1) == step


def test_verify_totp_accepts_one_step_of_drift_either_side() -> None:
    secret = security.new_totp_secret()
    totp = pyotp.TOTP(secret, interval=security.TOTP_INTERVAL)
    step = security.totp_step()

    assert security.verify_totp(secret, totp.at((step - 1) * security.TOTP_INTERVAL)) is not None
    assert security.verify_totp(secret, totp.at((step + 1) * security.TOTP_INTERVAL)) is not None
    assert security.verify_totp(secret, totp.at((step + 5) * security.TOTP_INTERVAL)) is None


def test_totp_uri_names_the_issuer_and_the_account() -> None:
    uri = security.totp_uri(security.new_totp_secret(), "person@test.local")

    assert uri.startswith("otpauth://totp/")
    assert "issuer=SaralPrivacy" in uri
    assert "person%40test.local" in uri


def test_totp_step_advances_every_thirty_seconds() -> None:
    now = datetime.now(UTC).timestamp()

    assert security.totp_step(now + 30) == security.totp_step(now) + 1
