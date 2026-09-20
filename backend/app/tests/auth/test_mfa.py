"""POST /auth/mfa/enroll and /auth/mfa/verify — the only place an access token is minted."""

import pyotp
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes.mfa import ALREADY_ENROLLED, CODE_FORMAT, CODE_MISMATCH, ENROLL_FIRST
from app.core import security
from app.crud import auth as crud
from app.models.auth import AuditLog, AuthSession
from app.tests.auth.fixtures import make_user, pending_bearer

ENROLL = "/api/v1/auth/mfa/enroll"
VERIFY = "/api/v1/auth/mfa/verify"


def _code_for(secret: str, offset_steps: int = 0) -> str:
    totp = pyotp.TOTP(secret, interval=security.TOTP_INTERVAL)
    step = security.totp_step() + offset_steps
    return str(totp.at(step * security.TOTP_INTERVAL))


# ── enroll ────────────────────────────────────────────────────────────────
def test_enroll_returns_a_qr_secret_and_uri_and_stores_it_encrypted(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="e1@test.local", role="admin", totp_confirmed=False)

    res = client.post(ENROLL, headers=pending_bearer(user), json={})

    assert res.status_code == 200
    body = res.json()
    assert body["qr"].startswith("data:image/svg+xml;base64,")
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert "issuer=SaralPrivacy" in body["otpauth_uri"]
    assert body["factorId"] == str(user.id)
    session.refresh(user)
    assert user.totp_secret_enc is not None
    assert user.totp_secret_enc != body["secret"]  # stored ciphertext, never the secret
    assert crud.get_totp_secret(user) == body["secret"]
    assert user.totp_confirmed is False


def test_enroll_refuses_a_second_factor_with_409(client: TestClient, session: Session) -> None:
    user = make_user(session, email="e2@test.local", role="admin", totp_confirmed=True)

    res = client.post(ENROLL, headers=pending_bearer(user), json={})

    assert res.status_code == 409
    assert res.json()["detail"] == {"error": ALREADY_ENROLLED, "step": "verify"}


def test_enroll_without_a_pending_token_is_401_with_step_login(client: TestClient) -> None:
    res = client.post(ENROLL, json={})

    assert res.status_code == 401
    assert res.json()["detail"]["step"] == "login"


def test_an_access_token_is_not_accepted_as_a_pending_token(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    # Purpose is part of the signature check, so the two token kinds never cross over.
    res = client.post(ENROLL, headers=admin_headers, json={})

    assert res.status_code == 401


# ── verify ────────────────────────────────────────────────────────────────
def test_verify_mints_an_access_token_and_a_session_row(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="v1@test.local", role="admin", totp_confirmed=False)
    secret = security.new_totp_secret()
    crud.store_totp_secret(session, user, secret)

    res = client.post(VERIFY, headers=pending_bearer(user), json={"code": _code_for(secret)})

    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "admin"
    assert body["token_type"] == "bearer"
    claims = security.decode_token(body["access_token"], "access")
    assert claims is not None
    rows = session.exec(select(AuthSession).where(AuthSession.user_id == user.id)).all()
    assert len(rows) == 1
    assert claims["jti"] == str(rows[0].id)
    session.refresh(user)
    assert user.totp_confirmed is True
    assert "login" in [a.action for a in session.exec(select(AuditLog)).all()]


def test_verify_rejects_a_malformed_code_with_400(client: TestClient, session: Session) -> None:
    user = make_user(session, email="v2@test.local", role="admin")

    res = client.post(VERIFY, headers=pending_bearer(user), json={"code": "12345"})

    assert res.status_code == 400
    assert res.json()["detail"] == CODE_FORMAT


def test_verify_rejects_a_wrong_code(client: TestClient, session: Session) -> None:
    user = make_user(session, email="v3@test.local", role="admin", totp_confirmed=False)
    secret = security.new_totp_secret()
    crud.store_totp_secret(session, user, secret)
    wrong = "000000" if _code_for(secret) != "000000" else "111111"

    res = client.post(VERIFY, headers=pending_bearer(user), json={"code": wrong})

    assert res.status_code == 401
    assert res.json()["detail"] == CODE_MISMATCH
    assert session.exec(select(AuthSession)).all() == []


def test_verify_refuses_a_replayed_code(client: TestClient, session: Session) -> None:
    user = make_user(session, email="v4@test.local", role="admin", totp_confirmed=False)
    secret = security.new_totp_secret()
    crud.store_totp_secret(session, user, secret)
    code = _code_for(secret)

    first = client.post(VERIFY, headers=pending_bearer(user), json={"code": code})
    second = client.post(VERIFY, headers=pending_bearer(user), json={"code": code})

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["detail"] == CODE_MISMATCH


def test_verify_accepts_a_code_from_the_previous_step_drift_window(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="v5@test.local", role="admin", totp_confirmed=False)
    secret = security.new_totp_secret()
    crud.store_totp_secret(session, user, secret)

    res = client.post(
        VERIFY, headers=pending_bearer(user), json={"code": _code_for(secret, offset_steps=-1)}
    )

    assert res.status_code == 200


def test_verify_before_enrollment_says_enroll_first(client: TestClient, session: Session) -> None:
    user = make_user(session, email="v6@test.local", role="admin", totp_confirmed=False)

    res = client.post(VERIFY, headers=pending_bearer(user), json={"code": "123456"})

    assert res.status_code == 401
    assert res.json()["detail"] == {"error": ENROLL_FIRST, "step": "enroll"}


def test_verify_rate_limit_returns_429_with_retry_after(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="v7@test.local", role="admin", totp_confirmed=False)
    crud.store_totp_secret(session, user, security.new_totp_secret())
    headers = pending_bearer(user)
    for _ in range(10):
        client.post(VERIFY, headers=headers, json={"code": "000000"})

    res = client.post(VERIFY, headers=headers, json={"code": "000000"})

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many attempts. Try again later."
    assert int(res.headers["retry-after"]) >= 1


def test_verify_refuses_an_inactive_user(client: TestClient, session: Session) -> None:
    user = make_user(session, email="v8@test.local", role="admin", is_active=False)

    res = client.post(VERIFY, headers=pending_bearer(user), json={"code": "123456"})

    # PendingUser itself refuses an inactive account, as the old restorePending did.
    assert res.status_code == 401
