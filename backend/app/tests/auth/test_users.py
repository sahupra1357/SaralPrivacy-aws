"""Admin-only user administration under /api/v1/users."""

import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.auth import AuditLog
from app.tests.auth.fixtures import bearer, make_user


def test_list_users_is_admin_only(
    client: TestClient, admin_headers: dict[str, str], blogger_headers: dict[str, str]
) -> None:
    ok = client.get("/api/v1/users", headers=admin_headers)
    denied = client.get("/api/v1/users", headers=blogger_headers)

    assert ok.status_code == 200
    assert any(u["email"] == "admin@test.local" for u in ok.json())
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Access denied."


def test_patch_user_deactivates_and_revokes_live_sessions(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t1@test.local", role="blogger")
    target_headers = bearer(session, target)

    res = client.patch(
        f"/api/v1/users/{target.id}", headers=admin_headers, json={"is_active": False}
    )

    assert res.status_code == 200
    assert res.json()["is_active"] is False
    assert client.get("/api/v1/auth/me", headers=target_headers).status_code == 401
    assert "user.update" in [a.action for a in session.exec(select(AuditLog)).all()]


def test_patch_user_changes_the_role(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t2@test.local", role="blogger")

    res = client.patch(f"/api/v1/users/{target.id}", headers=admin_headers, json={"role": "admin"})

    assert res.status_code == 200
    assert res.json()["role"] == "admin"


def test_patch_user_rejects_an_unknown_role(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t3@test.local", role="blogger")

    res = client.patch(
        f"/api/v1/users/{target.id}", headers=admin_headers, json={"role": "superuser"}
    )

    assert res.status_code == 400
    assert res.json()["detail"] == "Unknown role."


def test_patch_user_with_an_empty_body_is_400(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t4@test.local", role="blogger")

    res = client.patch(f"/api/v1/users/{target.id}", headers=admin_headers, json={})

    assert res.status_code == 400
    assert res.json()["detail"] == "Nothing to update."


def test_unknown_user_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.patch(
        f"/api/v1/users/{uuid.uuid4()}", headers=admin_headers, json={"is_active": True}
    )

    assert res.status_code == 404
    assert res.json()["detail"] == "Not found."


def test_revoke_sessions_counts_and_kills_them(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t5@test.local", role="blogger")
    first = bearer(session, target)
    bearer(session, target)

    res = client.post(f"/api/v1/users/{target.id}/revoke-sessions", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {"success": True, "revoked": 2}
    assert client.get("/api/v1/auth/me", headers=first).status_code == 401
    assert "user.revoke_sessions" in [a.action for a in session.exec(select(AuditLog)).all()]


def test_reset_totp_clears_the_secret_so_the_next_login_enrolls(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    target = make_user(session, email="t6@test.local", role="blogger", totp_confirmed=True)

    res = client.post(f"/api/v1/users/{target.id}/reset-totp", headers=admin_headers)

    assert res.status_code == 200
    session.refresh(target)
    assert target.totp_secret_enc is None
    assert target.totp_confirmed is False
    assert target.last_totp_step is None
    assert "user.reset_totp" in [a.action for a in session.exec(select(AuditLog)).all()]


def test_user_admin_routes_need_authentication(client: TestClient) -> None:
    uid = uuid.uuid4()
    assert client.post(f"/api/v1/users/{uid}/revoke-sessions").status_code == 401
    assert client.post(f"/api/v1/users/{uid}/reset-totp").status_code == 401
