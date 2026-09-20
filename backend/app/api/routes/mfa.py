"""Step 2 of the admin login: TOTP enrollment and verification.

Both routes take the 10-minute *pending* token from `/auth/login` as the bearer
(`deps.PendingUser`). `/verify` is the only place an access token is minted, and it
inserts the `app.sessions` row whose id becomes that token's `jti`.

The pending token carries only `sub` and `purpose="mfa"`; identity and role are always
re-read from the database, never taken from the token body — the same rule the old
`admin_mfa_pending` cookie followed.
"""

import base64
import logging
from io import BytesIO
from typing import Any, cast

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.deps import RATE_LIMIT_MESSAGE, PendingUser, RateLimit, SessionDep, client_ip
from app.core import security
from app.core.config import settings
from app.crud import auth as crud
from app.models.auth import ROLES

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/mfa", tags=["auth"])

ACCESS_DENIED = "Access denied."
ALREADY_ENROLLED = "A verification app is already set up for this account."
ENROLL_FAILED = "Could not start verification setup."
CODE_FORMAT = "Enter the 6-digit code."
CODE_MISMATCH = "Code did not match. Try again."
ENROLL_FIRST = "Set up your verification app first."


class EnrollOut(BaseModel):
    # `factorId` is kept (as the user id) so the login page's payload shape is unchanged.
    factorId: str
    qr: str
    secret: str
    otpauth_uri: str


class VerifyIn(BaseModel):
    code: str = ""
    factorId: str | None = None  # accepted and ignored; one factor per user now


class VerifyOut(BaseModel):
    success: bool = True
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_in: int


def qr_svg_data_url(uri: str) -> str:
    """An SVG QR code as a data: URL — what the login page's <img src> already expects."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{encoded}"


def _guard_role(user: Any) -> None:
    if user.role not in ROLES or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, ACCESS_DENIED)


@router.post(
    "/enroll",
    response_model=EnrollOut,
    dependencies=[Depends(RateLimit("admin-mfa-enroll", 10, 15 * 60, message=RATE_LIMIT_MESSAGE))],
)
def enroll(session: SessionDep, user: PendingUser) -> EnrollOut:
    _guard_role(user)
    # Adding a second factor is not something the login flow may do.
    if user.totp_confirmed and user.totp_secret_enc:
        raise HTTPException(status.HTTP_409_CONFLICT, {"error": ALREADY_ENROLLED, "step": "verify"})

    # An abandoned earlier attempt left an unconfirmed secret behind; this overwrites it.
    secret = security.new_totp_secret()
    crud.store_totp_secret(session, user, secret)
    uri = security.totp_uri(secret, user.email)
    try:
        qr = qr_svg_data_url(uri)
    except Exception as exc:  # noqa: BLE001
        log.error("qr generation failed for %s: %s", user.id, exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, ENROLL_FAILED) from exc
    return EnrollOut(factorId=str(user.id), qr=qr, secret=secret, otpauth_uri=uri)


@router.post(
    "/verify",
    response_model=VerifyOut,
    dependencies=[Depends(RateLimit("admin-mfa", 10, 15 * 60, message=RATE_LIMIT_MESSAGE))],
)
def verify(body: VerifyIn, request: Request, session: SessionDep, user: PendingUser) -> VerifyOut:
    _guard_role(user)
    code = body.code.strip()
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, CODE_FORMAT)

    secret = crud.get_totp_secret(user)
    if secret is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, {"error": ENROLL_FIRST, "step": "enroll"})

    # A code at or below the last accepted step is a replay, even inside the drift window.
    step = security.verify_totp(secret, code, user.last_totp_step)
    if step is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, CODE_MISMATCH)

    crud.confirm_totp(session, user, step)
    row = crud.create_session(
        session,
        user,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    crud.audit(session, "login", actor_user_id=user.id, target=str(row.id))
    token = security.create_access_token(
        user.id, cast(security.Role, user.role), row.id, name=user.display_name
    )
    return VerifyOut(
        access_token=token,
        role=user.role,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
