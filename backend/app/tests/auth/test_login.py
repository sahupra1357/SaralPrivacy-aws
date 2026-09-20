"""POST /auth/login, /auth/logout, GET /auth/me."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.routes.login import (
    ACCESS_DENIED,
    CREDENTIALS_REQUIRED,
    INVALID_CREDENTIALS,
    SESSION_EXPIRED,
)
from app.core import security
from app.crud import auth as crud
from app.models.auth import AuditLog, AuthSession
from app.tests.auth.fixtures import TEST_PASSWORD, bearer, make_user

LOGIN = "/api/v1/auth/login"


def _seed_blogger_account(session: Session, email: str, *, active: bool) -> None:
    session.execute(
        text("insert into ops.blogger_accounts (email, name, active) values (:e, :n, :a)"),
        {"e": email, "n": "Desk", "a": active},
    )
    session.commit()


def test_login_returns_verify_step_and_pending_token(client: TestClient, session: Session) -> None:
    user = make_user(session, email="a1@test.local", role="admin", totp_confirmed=True)

    res = client.post(LOGIN, json={"email": "A1@Test.Local", "password": TEST_PASSWORD})

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["step"] == "verify"
    assert body["role"] == "admin"
    claims = security.decode_token(body["pending_token"], "mfa")
    assert claims is not None
    assert claims["sub"] == str(user.id)


def test_login_returns_enroll_step_before_first_totp(client: TestClient, session: Session) -> None:
    make_user(session, email="a2@test.local", role="admin", totp_confirmed=False)

    res = client.post(LOGIN, json={"email": "a2@test.local", "password": TEST_PASSWORD})

    assert res.status_code == 200
    assert res.json()["step"] == "enroll"


def test_login_requires_email_and_password(client: TestClient) -> None:
    res = client.post(LOGIN, json={"email": "", "password": ""})
    assert res.status_code == 400
    assert res.json()["detail"] == CREDENTIALS_REQUIRED


def test_login_does_not_reveal_whether_the_account_exists(
    client: TestClient, session: Session
) -> None:
    make_user(session, email="a3@test.local", role="admin")

    unknown = client.post(LOGIN, json={"email": "nobody@test.local", "password": TEST_PASSWORD})
    wrong = client.post(LOGIN, json={"email": "a3@test.local", "password": "not-the-password"})

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"] == INVALID_CREDENTIALS


def test_login_rejects_inactive_user_with_access_denied(
    client: TestClient, session: Session
) -> None:
    make_user(session, email="a4@test.local", role="admin", is_active=False)

    res = client.post(LOGIN, json={"email": "a4@test.local", "password": TEST_PASSWORD})

    assert res.status_code == 403
    assert res.json()["detail"] == ACCESS_DENIED


def test_login_allows_a_blogger_whose_account_is_active(
    client: TestClient, session: Session
) -> None:
    make_user(session, email="b1@test.local", role="blogger")
    _seed_blogger_account(session, "b1@test.local", active=True)

    res = client.post(LOGIN, json={"email": "b1@test.local", "password": TEST_PASSWORD})

    assert res.status_code == 200
    assert res.json()["role"] == "blogger"


def test_login_rejects_a_revoked_blogger(client: TestClient, session: Session) -> None:
    make_user(session, email="b2@test.local", role="blogger")
    _seed_blogger_account(session, "b2@test.local", active=False)

    res = client.post(LOGIN, json={"email": "b2@test.local", "password": TEST_PASSWORD})

    assert res.status_code == 403
    assert res.json()["detail"] == ACCESS_DENIED


def test_login_rejects_a_blogger_with_no_directory_row(
    client: TestClient, session: Session
) -> None:
    make_user(session, email="b3@test.local", role="blogger")

    res = client.post(LOGIN, json={"email": "b3@test.local", "password": TEST_PASSWORD})

    assert res.status_code == 403
    assert res.json()["detail"] == ACCESS_DENIED


def test_login_rate_limit_returns_429_with_retry_after(
    client: TestClient, session: Session
) -> None:
    make_user(session, email="a5@test.local", role="admin")
    payload = {"email": "a5@test.local", "password": "wrong-password"}
    for _ in range(5):
        client.post(LOGIN, json=payload)

    res = client.post(LOGIN, json=payload)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many attempts. Try again later."
    assert int(res.headers["retry-after"]) >= 1


# ── /auth/me ──────────────────────────────────────────────────────────────
def test_me_returns_the_public_user_shape(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/v1/auth/me", headers=admin_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "admin@test.local"
    assert body["role"] == "admin"
    assert body["name"] == "Admin"
    assert "hashed_password" not in body
    assert "totp_secret_enc" not in body


def test_me_without_a_token_is_401(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


# ── /auth/logout ──────────────────────────────────────────────────────────
def test_logout_revokes_the_session_and_audits(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    res = client.post("/api/v1/auth/logout", headers=admin_headers)

    assert res.status_code == 200
    rows = session.exec(select(AuthSession)).all()
    assert all(r.revoked_at is not None for r in rows)
    actions = [a.action for a in session.exec(select(AuditLog)).all()]
    assert "logout" in actions


def test_a_revoked_session_can_no_longer_authenticate(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    client.post("/api/v1/auth/logout", headers=admin_headers)

    res = client.get("/api/v1/auth/me", headers=admin_headers)

    assert res.status_code == 401
    assert res.json()["detail"] == SESSION_EXPIRED


def test_a_token_whose_session_row_never_existed_is_401(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="a6@test.local", role="admin")
    orphan = security.create_access_token(user.id, "admin", uuid.uuid4())

    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {orphan}"})

    assert res.status_code == 401


def test_deactivating_a_user_kills_their_live_token(client: TestClient, session: Session) -> None:
    user = make_user(session, email="a7@test.local", role="admin")
    headers = bearer(session, user)
    crud.update_user(session, user, is_active=False)

    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
