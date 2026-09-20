"""Shared FastAPI dependencies — the contract every module builds on.

    SessionDep                  database session
    CurrentUser                 verified access JWT + live sessions row (401 otherwise)
    require_role("admin")       403 unless the user's role is listed
    RateLimit("contact", 5, 60) 429 with Retry-After, keyed by client IP (or user)
    client_ip(request)          rightmost X-Forwarded-For, else peer address

Auth tables live in the auth module (`app.models.auth`); this file imports them lazily
so core can be static-checked before that module exists.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NamedTuple
from urllib.parse import unquote
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.core import security
from app.core.db import get_session
from app.core.ratelimit import hit

SessionDep = Annotated[Session, Depends(get_session)]

_bearer = HTTPBearer(auto_error=False)
TokenDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]

RATE_LIMIT_MESSAGE = "Too many attempts. Try again later."
RATE_LIMIT_MESSAGE_PUBLIC = "Too many requests. Please wait a moment and try again."


def client_ip(request: Request) -> str:
    """The visitor's address, from headers the client cannot choose.

    1. CloudFront-Viewer-Address ("ip:port"; IPv6 is "a:b:...:h:port"), set by CloudFront
       in the AWS deployment. Behind CloudFront the rightmost X-Forwarded-For hop is the
       edge server, so XFF alone would put every visitor of one edge in one bucket.
    2. Otherwise the same rule as abuseGuard.ts: proxies append, so the rightmost XFF
       entry is the hop closest to us; anything the client wrote is further left.
    """
    viewer = request.headers.get("cloudfront-viewer-address", "").strip()
    if viewer and ":" in viewer:
        return viewer.rsplit(":", 1)[0].strip("[]")
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        last = xff.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


class Geo(NamedTuple):
    city: str
    country: str
    region: str


def client_geo(request: Request) -> Geo:
    """Visitor location from edge headers. CloudFront (AWS) sends CloudFront-Viewer-*
    when the distribution forwards them; Vercel sent x-vercel-ip-*. Empty when neither
    is present (for example local Docker)."""
    h = request.headers

    def pick(*names: str) -> str:
        for n in names:
            v = h.get(n)
            if v:
                return unquote(v)
        return ""

    return Geo(
        city=pick("cloudfront-viewer-city", "x-vercel-ip-city"),
        country=pick("cloudfront-viewer-country", "x-vercel-ip-country"),
        region=pick("cloudfront-viewer-country-region", "x-vercel-ip-country-region"),
    )


# ── Current user ──────────────────────────────────────────────────────────
def _load_user_and_session(session: Session, claims: dict[str, Any]) -> Any:
    # Lazy import: the auth module owns these models.
    from app.crud import auth as auth_crud  # noqa: PLC0415

    user = auth_crud.get_user(session, UUID(str(claims["sub"])))
    if user is None or not user.is_active:
        return None
    live = auth_crud.get_live_session(session, UUID(str(claims["jti"])))
    if live is None or live.user_id != user.id:
        return None
    return user


def get_current_user(session: SessionDep, creds: TokenDep) -> Any:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    claims = security.decode_token(creds.credentials, "access")
    if claims is None or "jti" not in claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please sign in again.")
    user = _load_user_and_session(session, claims)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired. Please sign in again.")
    return user


CurrentUser = Annotated[Any, Depends(get_current_user)]


def require_role(*roles: Literal["admin", "blogger"]) -> Callable[..., Any]:
    def _dep(user: CurrentUser) -> Any:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied.")
        return user

    return _dep


def get_pending_user(session: SessionDep, creds: TokenDep) -> Any:
    """For the MFA step: a valid *pending* token, user active. 401 with step:login otherwise."""
    from app.crud import auth as auth_crud  # noqa: PLC0415

    claims = security.decode_token(creds.credentials, "mfa") if creds else None
    user = auth_crud.get_user(session, UUID(str(claims["sub"]))) if claims else None
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"error": "Session expired. Please sign in again.", "step": "login"},
        )
    return user


PendingUser = Annotated[Any, Depends(get_pending_user)]


# ── Rate limiting ─────────────────────────────────────────────────────────
class RateLimit:
    """Dependency: `dependencies=[Depends(RateLimit("contact", 5, 60))]`."""

    def __init__(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        by: Literal["ip", "user"] = "ip",
        message: str = RATE_LIMIT_MESSAGE_PUBLIC,
    ) -> None:
        self.key, self.limit, self.window, self.by, self.message = (
            key,
            limit,
            window_seconds,
            by,
            message,
        )

    def __call__(self, request: Request, response: Response, session: SessionDep) -> None:
        if self.by == "user":
            ident = getattr(getattr(request.state, "user", None), "id", None) or client_ip(request)
        else:
            ident = client_ip(request)
        result = hit(session, f"{self.key}:{ident}", self.limit, self.window)
        if not result.ok:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                self.message,
                headers={"Retry-After": str(result.retry_after)},
            )


def now_utc() -> datetime:
    return datetime.now(UTC)
