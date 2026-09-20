"""POST /auth/invite (admin only) and POST /auth/recover (never enumerates)."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes.login import RECOVER_ALWAYS
from app.core.config import settings
from app.crud import auth as crud
from app.models.auth import AuditLog, InviteToken
from app.tests.auth.fixtures import make_user

INVITE = "/api/v1/auth/invite"
RECOVER = "/api/v1/auth/recover"


def test_invite_creates_the_user_the_token_and_the_email(
    client: TestClient, session: Session, admin_headers: dict[str, str], mock_email: object
) -> None:
    res = client.post(
        INVITE, headers=admin_headers, json={"email": "New@Test.local", "name": "New Writer"}
    )

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["email_sent"] is True
    assert body["invite_url"].startswith(
        f"{settings.FRONTEND_HOST.rstrip('/')}/admin/set-password?token="
    )
    assert body["invite_url"].endswith("&type=invite")

    created = crud.get_user_by_email(session, "new@test.local")
    assert created is not None
    assert created.role == "blogger"
    assert created.display_name == "New Writer"
    assert created.totp_confirmed is False
    assert session.exec(select(InviteToken).where(InviteToken.user_id == created.id)).one()
    assert "invite" in [a.action for a in session.exec(select(AuditLog)).all()]

    sent = mock_email.last()
    assert sent["to"] == ["new@test.local"]
    assert sent["subject"] == "You've been invited to contribute to SaralPrivacy Insights"
    assert "Set Up My Account" in sent["html"]


def test_invite_uses_the_admin_subject_for_an_admin_invite(
    client: TestClient, admin_headers: dict[str, str], mock_email: object
) -> None:
    client.post(
        INVITE,
        headers=admin_headers,
        json={"email": "boss@test.local", "name": "Boss", "role": "admin"},
    )

    assert mock_email.last()["subject"] == "Set up your SaralPrivacy admin account"


def test_invite_requires_email_and_name(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(INVITE, headers=admin_headers, json={"email": "x@test.local", "name": " "})

    assert res.status_code == 400
    assert res.json()["detail"] == "Email and name are required."


def test_invite_rejects_a_duplicate_email(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    make_user(session, email="dupe@test.local", role="blogger")

    res = client.post(
        INVITE, headers=admin_headers, json={"email": "dupe@test.local", "name": "Dupe"}
    )

    assert res.status_code == 409
    assert res.json()["detail"] == "An account with this email already exists."


def test_invite_is_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    res = client.post(INVITE, headers=blogger_headers, json={"email": "x@test.local", "name": "X"})

    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied."


def test_invite_needs_authentication(client: TestClient) -> None:
    assert client.post(INVITE, json={"email": "x@test.local", "name": "X"}).status_code == 401


# ── recovery ──────────────────────────────────────────────────────────────
def test_recover_sends_a_one_hour_link_for_a_known_account(
    client: TestClient, session: Session, mock_email: object
) -> None:
    user = make_user(session, email="r1@test.local", role="admin")

    res = client.post(RECOVER, json={"email": "R1@Test.local"})

    assert res.status_code == 200
    assert res.json()["message"] == RECOVER_ALWAYS
    row = session.exec(select(InviteToken).where(InviteToken.user_id == user.id)).one()
    assert row.purpose == "recovery"
    sent = mock_email.last()
    assert sent["subject"] == "Reset your SaralPrivacy admin password"
    assert "type=recovery" in sent["html"]
    assert "Choose a New Password" in sent["html"]
    assert "valid for <strong>1 hour</strong>" in sent["html"]


def test_recover_answers_the_same_for_an_unknown_account_and_sends_nothing(
    client: TestClient, session: Session, mock_email: object
) -> None:
    res = client.post(RECOVER, json={"email": "ghost@test.local"})

    assert res.status_code == 200
    assert res.json()["message"] == RECOVER_ALWAYS
    assert session.exec(select(InviteToken)).all() == []
    assert mock_email.calls == []


def test_recover_rate_limit_returns_429(client: TestClient) -> None:
    for _ in range(5):
        client.post(RECOVER, json={"email": "ghost@test.local"})

    res = client.post(RECOVER, json={"email": "ghost@test.local"})

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many attempts. Try again later."
