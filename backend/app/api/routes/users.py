"""Admin-only user administration: deactivate, change role, revoke sessions, reset TOTP.

Replaces `lib/auth/adminAuth.setAuthUserBanned()` (a Supabase ban) and gives the admin UI
the two levers the Supabase console used to provide: kill every live session, and clear a
lost authenticator. Every call writes an `audit_log` row.
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import SessionDep
from app.api.routes.login import AdminUser, UserOut, to_user_out
from app.crud import auth as crud
from app.models.auth import ROLES, User

router = APIRouter(prefix="/users", tags=["users"])

NOT_FOUND = "Not found."
NOTHING_TO_UPDATE = "Nothing to update."
BAD_ROLE = "Unknown role."


class UserPatch(BaseModel):
    is_active: bool | None = None
    role: str | None = None
    display_name: str | None = None


class RevokeOut(BaseModel):
    success: bool = True
    revoked: int


class OkOut(BaseModel):
    success: bool = True


def _load(session: SessionDep, user_id: uuid.UUID) -> User:
    user = crud.get_user(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return user


@router.get("", response_model=list[UserOut])
def list_users(session: SessionDep, actor: AdminUser) -> list[UserOut]:  # noqa: ARG001
    rows = session.exec(select(User).order_by(User.created_at.desc())).all()  # type: ignore[attr-defined]
    return [to_user_out(u) for u in rows]


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: uuid.UUID, body: UserPatch, session: SessionDep, actor: AdminUser
) -> UserOut:
    user = _load(session, user_id)
    if body.is_active is None and body.role is None and body.display_name is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, NOTHING_TO_UPDATE)
    if body.role is not None and body.role not in ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, BAD_ROLE)

    crud.update_user(
        session, user, is_active=body.is_active, role=body.role, display_name=body.display_name
    )
    # Deactivating must take effect now, not when the token happens to expire.
    if body.is_active is False:
        crud.revoke_all_sessions(session, user.id)
    crud.audit(
        session,
        "user.update",
        actor_user_id=actor.id,
        target=str(user.id),
        details={"is_active": body.is_active, "role": body.role},
    )
    return to_user_out(user)


@router.post("/{user_id}/revoke-sessions", response_model=RevokeOut)
def revoke_sessions(user_id: uuid.UUID, session: SessionDep, actor: AdminUser) -> RevokeOut:
    user = _load(session, user_id)
    count = crud.revoke_all_sessions(session, user.id)
    crud.audit(
        session,
        "user.revoke_sessions",
        actor_user_id=actor.id,
        target=str(user.id),
        details={"revoked": count},
    )
    return RevokeOut(revoked=count)


@router.post("/{user_id}/reset-totp", response_model=OkOut)
def reset_totp(user_id: uuid.UUID, session: SessionDep, actor: AdminUser) -> OkOut:
    """Clear the authenticator so the next sign-in goes through enrollment again."""
    user = _load(session, user_id)
    crud.reset_totp(session, user)
    crud.revoke_all_sessions(session, user.id)
    crud.audit(session, "user.reset_totp", actor_user_id=actor.id, target=str(user.id))
    return OkOut()
