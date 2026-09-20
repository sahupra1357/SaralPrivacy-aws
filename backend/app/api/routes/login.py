"""Step 1 of the admin login, plus session, invite, recovery and set-password.

A correct password is NOT a session. It yields a 10-minute *pending* token; the access
token is minted only by `/auth/mfa/verify` once a TOTP code checks out. That is the same
two-step shape the Supabase implementation had, with our own tables underneath.

Messages are the ones the TypeScript returned, verbatim (see docs/build/inventory/auth.md).
"""

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import (
    RATE_LIMIT_MESSAGE,
    CurrentUser,
    RateLimit,
    SessionDep,
    TokenDep,
    require_role,
)
from app.core import security
from app.core.config import settings
from app.crud import auth as crud
from app.models.auth import ROLES, AuthSession, User
from app.services import email as email_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Invalid credentials."
ACCESS_DENIED = "Access denied."
SESSION_EXPIRED = "Session expired. Please sign in again."
CREDENTIALS_REQUIRED = "Email and password are required."
ALL_FIELDS_REQUIRED = "All fields are required."
BAD_LINK = "This link is invalid or has expired. Ask the admin for a new one."
PASSWORD_RULE = f"Password must be {security.MIN_PASSWORD}–{security.MAX_PASSWORD} characters."
RECOVER_ALWAYS = "If that account exists, an email has been sent."

INVITE_TTL_HOURS = 24
RECOVERY_TTL_HOURS = 1


# ── Schemas ───────────────────────────────────────────────────────────────
class LoginIn(BaseModel):
    """Defaults instead of required fields: the TypeScript answered a missing field with
    400 and its own message, not a 422 validation envelope."""

    email: str = ""
    password: str = ""


class LoginOut(BaseModel):
    success: bool = True
    step: Literal["enroll", "verify"]
    role: str
    pending_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    name: str | None = None
    is_active: bool
    totp_confirmed: bool


class OkOut(BaseModel):
    success: bool = True


class InviteIn(BaseModel):
    email: str = ""
    name: str = ""
    role: str = "blogger"


class InviteOut(BaseModel):
    success: bool = True
    user_id: uuid.UUID
    invite_url: str
    email_sent: bool
    email_error: str | None = None


class RecoverIn(BaseModel):
    email: str = ""


class RecoverOut(BaseModel):
    success: bool = True
    message: str = RECOVER_ALWAYS


class SetPasswordIn(BaseModel):
    token: str = ""
    type: str = ""
    password: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────
def to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        name=user.display_name,
        is_active=user.is_active,
        totp_confirmed=user.totp_confirmed,
    )


def get_current_session(session: SessionDep, creds: TokenDep) -> AuthSession:
    """The live `sessions` row behind the bearer token — what logout needs to revoke."""
    claims = security.decode_token(creds.credentials, "access") if creds else None
    if claims is None or "jti" not in claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED)
    row = crud.get_live_session(session, uuid.UUID(str(claims["jti"])))
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, SESSION_EXPIRED)
    return row


CurrentSession = Annotated[AuthSession, Depends(get_current_session)]
AdminUser = Annotated[Any, Depends(require_role("admin"))]


def set_password_url(token: str, kind: str) -> str:
    return f"{settings.FRONTEND_HOST.rstrip('/')}/admin/set-password?token={token}&type={kind}"


def _invite_subject(kind: str, role: str) -> str:
    if kind == "recovery":
        return "Reset your SaralPrivacy admin password"
    if role == "admin":
        return "Set up your SaralPrivacy admin account"
    return "You've been invited to contribute to SaralPrivacy Insights"


def send_link_email(
    *, to: str, name: str, role: str, kind: str, url: str, valid_for: str
) -> tuple[bool, str | None]:
    """Best effort, exactly as the bloggers route was: the caller still gets the URL so an
    admin can pass it on by hand when mail is down."""
    html = email_service.render(
        "auth_invite.html", kind=kind, role=role, name=name, invite_url=url, valid_for=valid_for
    )
    try:
        email_service.send(to, _invite_subject(kind, role), html)
    except Exception as exc:  # noqa: BLE001 — never fail the request on a mail problem
        log.error("auth link email failed (%s): %s", kind, exc)
        return False, str(exc)
    return True, None


# ── Routes ────────────────────────────────────────────────────────────────
@router.post(
    "/login",
    response_model=LoginOut,
    dependencies=[Depends(RateLimit("admin-login", 5, 15 * 60, message=RATE_LIMIT_MESSAGE))],
)
def login(body: LoginIn, session: SessionDep) -> LoginOut:
    email = body.email.strip().lower()
    password = body.password
    if not email or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, CREDENTIALS_REQUIRED)

    user = crud.get_user_by_email(session, email)
    # Same message for unknown email and wrong password — no enumeration.
    if user is None or not security.verify_password(password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    # Past this point the password was right, so the caller already knows the account
    # exists; a role/activation problem may say so plainly, as it did before.
    if user.role not in ROLES or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCESS_DENIED)
    # Bloggers can be revoked from the admin UI (ops.blogger_accounts.active).
    if user.role == "blogger" and not crud.blogger_is_active(session, user.email):
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCESS_DENIED)

    step = "verify" if (user.totp_confirmed and user.totp_secret_enc) else "enroll"
    return LoginOut(step=step, role=user.role, pending_token=security.create_pending_token(user.id))


@router.post("/logout", response_model=OkOut)
def logout(session: SessionDep, current: CurrentSession) -> OkOut:
    crud.revoke_session(session, current.id)
    crud.audit(session, "logout", actor_user_id=current.user_id, target=str(current.id))
    return OkOut()


@router.get("/me", response_model=UserOut)
def read_me(user: CurrentUser) -> UserOut:
    return to_user_out(user)


@router.post("/invite", response_model=InviteOut)
def invite(body: InviteIn, session: SessionDep, actor: AdminUser) -> InviteOut:
    email = body.email.strip().lower()
    name = body.name.strip()
    if not email or not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email and name are required.")
    role = body.role if body.role in ROLES else "blogger"

    if crud.get_user_by_email(session, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")

    user = crud.create_user(session, email=email, role=role, display_name=name)
    token, _row = crud.create_invite_token(session, user.id, "invite", INVITE_TTL_HOURS)
    url = set_password_url(token, "invite")
    sent, err = send_link_email(
        to=email, name=name, role=role, kind="invite", url=url, valid_for="24 hours"
    )
    crud.audit(
        session, "invite", actor_user_id=actor.id, target=str(user.id), details={"role": role}
    )
    return InviteOut(user_id=user.id, invite_url=url, email_sent=sent, email_error=err)


@router.post(
    "/recover",
    response_model=RecoverOut,
    dependencies=[Depends(RateLimit("auth-recover", 5, 15 * 60, message=RATE_LIMIT_MESSAGE))],
)
def recover(body: RecoverIn, session: SessionDep) -> RecoverOut:
    """Always the same answer, whatever the email. Enumeration here would leak the whole
    admin roster, and the old flow never exposed it either."""
    email = body.email.strip().lower()
    user = crud.get_user_by_email(session, email) if email else None
    if user is not None and user.is_active:
        token, _row = crud.create_invite_token(session, user.id, "recovery", RECOVERY_TTL_HOURS)
        url = set_password_url(token, "recovery")
        send_link_email(
            to=user.email,
            name=user.display_name or "",
            role=user.role,
            kind="recovery",
            url=url,
            valid_for="1 hour",
        )
        crud.audit(session, "recover", actor_user_id=user.id, target=user.email)
    return RecoverOut()


@router.post(
    "/set-password",
    response_model=OkOut,
    dependencies=[Depends(RateLimit("set-password", 10, 15 * 60, message=RATE_LIMIT_MESSAGE))],
)
def set_password(body: SetPasswordIn, session: SessionDep) -> OkOut:
    token = body.token.strip()
    kind = body.type if body.type in ("invite", "recovery") else ""
    password = body.password
    if not token or not kind or not password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, ALL_FIELDS_REQUIRED)
    if not security.password_ok(password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, PASSWORD_RULE)

    row = crud.find_usable_invite_token(session, token, kind)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LINK)
    user = crud.get_user(session, row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LINK)

    crud.set_password(session, user, password)
    crud.mark_invite_token_used(session, row)
    # Changing a password logs every other device out.
    crud.revoke_all_sessions(session, user.id)

    # A blogger completing an INVITE becomes active in the admin list. Recovery never
    # changes activation — a revoked blogger stays revoked.
    if kind == "invite" and user.role == "blogger":
        try:
            crud.activate_blogger(session, user.email)
        except Exception as exc:  # noqa: BLE001 — the password is already set; do not fail
            log.error("blogger activation failed for %s: %s", user.id, exc)

    crud.audit(session, "set_password", actor_user_id=user.id, target=kind)
    return OkOut()
