"""/admin/bloggers — list, invite, revoke/restore, delete."""

import html
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.crud import auth as auth_crud
from app.models.admin import BloggerAccount
from app.models.auth import AuthSession, InviteToken, User
from app.tests.auth.fixtures import bearer, make_user

URL = "/api/v1/admin/bloggers"


def _invite(client: TestClient, headers: dict[str, str], **body: str) -> dict:
    payload = {"email": "Writer@Example.com ", "name": " Asha Writer ", "bio": " Privacy nerd "}
    payload.update(body)
    res = client.post(URL, json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_invite_creates_account_directory_row_and_emails_the_link(
    client: TestClient, session: Session, admin_headers: dict[str, str], mock_email
) -> None:
    body = _invite(client, admin_headers)

    assert body["success"] is True
    assert body["emailSent"] is True
    assert body["emailError"] is None
    assert "/admin/set-password?token=" in body["inviteUrl"]
    assert body["inviteUrl"].endswith("&type=invite")

    user = auth_crud.get_user_by_email(session, "writer@example.com")
    assert user is not None and user.role == "blogger" and user.display_name == "Asha Writer"
    tokens = session.exec(select(InviteToken).where(InviteToken.user_id == user.id)).all()
    assert [t.purpose for t in tokens] == ["invite"]

    row = session.get(BloggerAccount, uuid.UUID(body["id"]))
    assert row is not None
    assert (row.email, row.name, row.bio) == ("writer@example.com", "Asha Writer", "Privacy nerd")
    assert row.active is False
    assert row.invite_token == "pending"
    assert row.password_hash == "" and row.token_expires == ""
    assert row.created_at_attr and row.created_at_attr.endswith("Z")

    sent = mock_email.last()
    assert sent["to"] == ["writer@example.com"]
    assert sent["subject"] == "You've been invited to contribute to SaralPrivacy Insights"
    # Jinja autoescapes the link inside href/text, so "&" appears as "&amp;" in the HTML.
    assert html.escape(body["inviteUrl"]) in sent["html"]


def test_invite_requires_email_and_name(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(URL, json={"email": "a@b.c"}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json()["detail"] == "Email and name are required."


def test_invite_rejects_an_existing_blogger_row(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _invite(client, admin_headers)
    res = client.post(
        URL, json={"email": "writer@example.com", "name": "Again"}, headers=admin_headers
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "A blogger with this email already exists."


def test_invite_rejects_an_existing_account_without_a_row(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    make_user(session, email="taken@example.com", role="admin")
    res = client.post(URL, json={"email": "taken@example.com", "name": "X"}, headers=admin_headers)
    assert res.status_code == 409


def test_invite_returns_the_url_even_when_email_fails(
    client: TestClient, admin_headers: dict[str, str], monkeypatch
) -> None:
    from app.services import email

    def boom(*_a, **_k):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email, "send", boom)
    body = _invite(client, admin_headers)
    assert body["emailSent"] is False
    assert body["emailError"] == "smtp down"
    assert body["inviteUrl"]


def test_list_returns_documents_newest_first(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _invite(client, admin_headers, email="one@example.com", name="One")
    _invite(client, admin_headers, email="two@example.com", name="Two")

    res = client.get(URL, headers=admin_headers)

    assert res.status_code == 200
    bloggers = res.json()["bloggers"]
    assert [b["name"] for b in bloggers] == ["Two", "One"]
    assert bloggers[0]["$id"] == bloggers[0]["id"]
    assert bloggers[0]["invite_token"] == "pending"
    assert bloggers[0]["created_at"].endswith("Z")  # the attribute, not the system column


def test_routes_are_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.get(URL, headers=blogger_headers).status_code == 403
    assert client.post(URL, json={}, headers=blogger_headers).status_code == 403
    assert client.get(URL).status_code == 401


def test_deactivate_flips_row_disables_account_and_revokes_sessions(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    body = _invite(client, admin_headers)
    user = auth_crud.get_user_by_email(session, "writer@example.com")
    assert user is not None
    blogger_headers = bearer(session, user)

    res = client.patch(f"{URL}/{body['id']}", json={"active": False}, headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {"success": True}
    session.expire_all()
    row = session.get(BloggerAccount, uuid.UUID(body["id"]))
    assert row is not None and row.active is False and row.invite_token == ""
    assert session.get(User, user.id).is_active is False  # type: ignore[union-attr]
    live = session.exec(
        select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))  # type: ignore[union-attr]
    ).all()
    assert live == []
    assert client.get("/api/v1/auth/me", headers=blogger_headers).status_code == 401


def test_restore_reactivates_row_and_account(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    body = _invite(client, admin_headers)
    client.patch(f"{URL}/{body['id']}", json={"active": False}, headers=admin_headers)

    res = client.patch(f"{URL}/{body['id']}", json={"active": True}, headers=admin_headers)

    assert res.status_code == 200
    session.expire_all()
    assert session.get(BloggerAccount, uuid.UUID(body["id"])).active is True  # type: ignore[union-attr]
    user = auth_crud.get_user_by_email(session, "writer@example.com")
    assert user is not None and user.is_active is True


def test_patch_only_true_activates(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    body = _invite(client, admin_headers)
    client.patch(f"{URL}/{body['id']}", json={"active": "yes"}, headers=admin_headers)
    session.expire_all()
    assert session.get(BloggerAccount, uuid.UUID(body["id"])).active is False  # type: ignore[union-attr]


def test_delete_removes_row_and_account(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    body = _invite(client, admin_headers)

    res = client.delete(f"{URL}/{body['id']}", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {"success": True}
    session.expire_all()
    assert session.get(BloggerAccount, uuid.UUID(body["id"])) is None
    assert auth_crud.get_user_by_email(session, "writer@example.com") is None


def test_delete_then_reinvite_is_the_resend_flow(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    body = _invite(client, admin_headers)
    client.delete(f"{URL}/{body['id']}", headers=admin_headers)
    again = _invite(client, admin_headers)
    assert again["id"] != body["id"]


def test_legacy_ids_resolve_and_unknown_ids_are_a_no_op(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    row = BloggerAccount(
        email="legacy@example.com", name="Legacy", active=True, legacy_id="6650abc123"
    )
    session.add(row)
    session.commit()

    patched = client.patch(f"{URL}/6650abc123", json={"active": False}, headers=admin_headers)
    missing = client.delete(f"{URL}/does-not-exist", headers=admin_headers)

    assert patched.status_code == 200
    session.refresh(row)
    assert row.active is False
    assert missing.status_code == 200
    assert missing.json() == {"success": True}
