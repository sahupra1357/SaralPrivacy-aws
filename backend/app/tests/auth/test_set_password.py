"""POST /auth/set-password — completing an invite or a recovery link."""

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.api.routes.login import ALL_FIELDS_REQUIRED, BAD_LINK, PASSWORD_RULE
from app.core import security
from app.crud import auth as crud
from app.models.auth import AuthSession, InviteToken
from app.tests.auth.fixtures import bearer, make_user

SET_PASSWORD = "/api/v1/auth/set-password"
NEW_PASSWORD = "brand-new-password-1"


def test_set_password_accepts_an_invite_token_once(client: TestClient, session: Session) -> None:
    user = make_user(session, email="s1@test.local", role="admin")
    token, _ = crud.create_invite_token(session, user.id, "invite", 24)

    first = client.post(
        SET_PASSWORD, json={"token": token, "type": "invite", "password": NEW_PASSWORD}
    )
    second = client.post(
        SET_PASSWORD, json={"token": token, "type": "invite", "password": NEW_PASSWORD}
    )

    assert first.status_code == 200
    assert first.json() == {"success": True}
    assert second.status_code == 401
    assert second.json()["detail"] == BAD_LINK
    session.refresh(user)
    assert security.verify_password(NEW_PASSWORD, user.hashed_password)
    row = session.exec(select(InviteToken)).one()
    assert row.used_at is not None


def test_set_password_revokes_every_live_session(client: TestClient, session: Session) -> None:
    user = make_user(session, email="s2@test.local", role="admin")
    headers = bearer(session, user)
    token, _ = crud.create_invite_token(session, user.id, "recovery", 1)

    client.post(SET_PASSWORD, json={"token": token, "type": "recovery", "password": NEW_PASSWORD})

    rows = session.exec(select(AuthSession).where(AuthSession.user_id == user.id)).all()
    assert all(r.revoked_at is not None for r in rows)
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_set_password_requires_every_field(client: TestClient) -> None:
    for payload in (
        {"token": "", "type": "invite", "password": NEW_PASSWORD},
        {"token": "t", "type": "", "password": NEW_PASSWORD},
        {"token": "t", "type": "invite", "password": ""},
        {"token": "t", "type": "nonsense", "password": NEW_PASSWORD},
    ):
        res = client.post(SET_PASSWORD, json=payload)
        assert res.status_code == 400
        assert res.json()["detail"] == ALL_FIELDS_REQUIRED


def test_set_password_enforces_the_length_rule(client: TestClient, session: Session) -> None:
    user = make_user(session, email="s3@test.local", role="admin")
    token, _ = crud.create_invite_token(session, user.id, "invite", 24)

    short = client.post(SET_PASSWORD, json={"token": token, "type": "invite", "password": "a" * 11})
    long = client.post(SET_PASSWORD, json={"token": token, "type": "invite", "password": "a" * 129})

    assert short.status_code == long.status_code == 400
    assert short.json()["detail"] == PASSWORD_RULE
    assert PASSWORD_RULE == "Password must be 12–128 characters."


def test_set_password_rejects_a_token_of_the_wrong_type(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="s4@test.local", role="admin")
    token, _ = crud.create_invite_token(session, user.id, "invite", 24)

    res = client.post(
        SET_PASSWORD, json={"token": token, "type": "recovery", "password": NEW_PASSWORD}
    )

    assert res.status_code == 401
    assert res.json()["detail"] == BAD_LINK


def test_set_password_rejects_an_expired_token(client: TestClient, session: Session) -> None:
    user = make_user(session, email="s5@test.local", role="admin")
    token, row = crud.create_invite_token(session, user.id, "invite", 24)
    session.execute(
        text("update app.invite_tokens set expires_at = now() - interval '1 minute' where id = :i"),
        {"i": str(row.id)},
    )
    session.commit()

    res = client.post(
        SET_PASSWORD, json={"token": token, "type": "invite", "password": NEW_PASSWORD}
    )

    assert res.status_code == 401
    assert res.json()["detail"] == BAD_LINK


def test_invite_activates_a_blogger_but_recovery_does_not(
    client: TestClient, session: Session
) -> None:
    user = make_user(session, email="s6@test.local", role="blogger")
    session.execute(
        text("insert into ops.blogger_accounts (email, name, active) values (:e, 'Desk', false)"),
        {"e": user.email},
    )
    session.commit()

    recovery_token, _ = crud.create_invite_token(session, user.id, "recovery", 1)
    client.post(
        SET_PASSWORD, json={"token": recovery_token, "type": "recovery", "password": NEW_PASSWORD}
    )
    assert crud.blogger_is_active(session, user.email) is False

    invite_token, _ = crud.create_invite_token(session, user.id, "invite", 24)
    client.post(
        SET_PASSWORD, json={"token": invite_token, "type": "invite", "password": NEW_PASSWORD}
    )
    assert crud.blogger_is_active(session, user.email) is True


def test_set_password_rate_limit_returns_429(client: TestClient) -> None:
    payload = {"token": "no-such-token", "type": "invite", "password": NEW_PASSWORD}
    for _ in range(10):
        client.post(SET_PASSWORD, json=payload)

    res = client.post(SET_PASSWORD, json=payload)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many attempts. Try again later."
    assert int(res.headers["retry-after"]) >= 1
