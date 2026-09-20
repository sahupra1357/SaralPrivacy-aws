"""Queries for the auth tables. No HTTP concepts, no business rules beyond the query.

`get_user`, `get_live_session` and `ensure_first_admin` are the three names the core
module already imports (`app/api/deps.py`, `app/initial_data.py`); their signatures are
fixed by that contract.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlmodel import Session, select

from app.core import security
from app.core.config import settings
from app.models.auth import AuditLog, AuthSession, InviteToken, User

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    """Postgres hands timestamptz back aware, SQLite/fixtures sometimes naive."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ── Users ─────────────────────────────────────────────────────────────────
def get_user(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def get_user_by_email(session: Session, email: str) -> User | None:
    normalised = email.strip().lower()
    return session.exec(select(User).where(User.email == normalised)).first()


def create_user(
    session: Session,
    *,
    email: str,
    role: str,
    password: str | None = None,
    display_name: str | None = None,
    is_active: bool = True,
) -> User:
    """A user with no password gets an unguessable one, so the row can never be signed
    into until the invite link sets a real password."""
    raw = password if password is not None else security.new_opaque_token()
    user = User(
        email=email.strip().lower(),
        hashed_password=security.hash_password(raw),
        role=role,
        display_name=display_name,
        is_active=is_active,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_password(session: Session, user: User, password: str) -> User:
    user.hashed_password = security.hash_password(password)
    user.updated_at = _now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def update_user(
    session: Session,
    user: User,
    *,
    is_active: bool | None = None,
    role: str | None = None,
    display_name: str | None = None,
) -> User:
    if is_active is not None:
        user.is_active = is_active
    if role is not None:
        user.role = role
    if display_name is not None:
        user.display_name = display_name
    user.updated_at = _now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def store_totp_secret(session: Session, user: User, secret: str) -> User:
    """Start (or restart) enrollment: the secret is only trusted once a code verifies."""
    user.totp_secret_enc = security.encrypt_secret(secret)
    user.totp_confirmed = False
    user.last_totp_step = None
    user.updated_at = _now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def get_totp_secret(user: User) -> str | None:
    if not user.totp_secret_enc:
        return None
    return security.decrypt_secret(user.totp_secret_enc)


def confirm_totp(session: Session, user: User, step: int) -> User:
    user.totp_confirmed = True
    user.last_totp_step = step
    user.updated_at = _now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def reset_totp(session: Session, user: User) -> User:
    user.totp_secret_enc = None
    user.totp_confirmed = False
    user.last_totp_step = None
    user.updated_at = _now()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ── Sessions ──────────────────────────────────────────────────────────────
def get_live_session(session: Session, session_id: uuid.UUID) -> AuthSession | None:
    """The session row, only while it is unrevoked and unexpired. `deps.CurrentUser`
    calls this on every authenticated request, which is what makes revocation instant."""
    row = session.get(AuthSession, session_id)
    if row is None or row.revoked_at is not None:
        return None
    expires = _aware(row.expires_at)
    if expires is not None and expires <= _now():
        return None
    return row


def create_session(
    session: Session,
    user: User,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> AuthSession:
    now = _now()
    row = AuthSession(
        user_id=user.id,
        issued_at=now,
        expires_at=now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        ip=ip,
        user_agent=(user_agent or None),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def revoke_session(session: Session, session_id: uuid.UUID) -> bool:
    row = session.get(AuthSession, session_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = _now()
    session.add(row)
    session.commit()
    return True


def revoke_all_sessions(session: Session, user_id: uuid.UUID) -> int:
    rows = session.exec(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))  # type: ignore[union-attr]
    ).all()
    now = _now()
    for row in rows:
        row.revoked_at = now
        session.add(row)
    if rows:
        session.commit()
    return len(rows)


# ── Invite / recovery tokens ──────────────────────────────────────────────
def create_invite_token(
    session: Session, user_id: uuid.UUID, purpose: str, ttl_hours: int
) -> tuple[str, InviteToken]:
    """Returns the clear token (emailed once) and the stored row (hash only)."""
    token = security.new_opaque_token()
    row = InviteToken(
        user_id=user_id,
        purpose=purpose,
        token_hash=security.hash_opaque_token(token),
        expires_at=_now() + timedelta(hours=ttl_hours),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return token, row


def find_usable_invite_token(session: Session, token: str, purpose: str) -> InviteToken | None:
    row = session.exec(
        select(InviteToken).where(InviteToken.token_hash == security.hash_opaque_token(token))
    ).first()
    if row is None or row.purpose != purpose or row.used_at is not None:
        return None
    expires = _aware(row.expires_at)
    if expires is not None and expires <= _now():
        return None
    return row


def mark_invite_token_used(session: Session, row: InviteToken) -> InviteToken:
    row.used_at = _now()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── Audit ─────────────────────────────────────────────────────────────────
def audit(
    session: Session,
    action: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    target: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(action=action, actor_user_id=actor_user_id, target=target, details=details)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# ── ops.blogger_accounts (owned by the admin module — read with narrow SQL) ─
_BLOGGER_ACTIVE = text(
    "select active from ops.blogger_accounts where lower(email) = :email limit 1"
)
_BLOGGER_ACTIVATE = text(
    "update ops.blogger_accounts set active = true, invite_token = '', updated_at = now() "
    "where lower(email) = :email"
)


def blogger_is_active(session: Session, email: str) -> bool:
    """`ops.blogger_accounts.active` is the flag the admin UI toggles; a blogger whose
    row is missing or not active cannot sign in, exactly as before."""
    row = session.execute(_BLOGGER_ACTIVE, {"email": email.strip().lower()}).first()
    return bool(row and row[0] is True)


def activate_blogger(session: Session, email: str) -> None:
    """A blogger completing an INVITE becomes active. Recovery never activates anyone."""
    session.execute(_BLOGGER_ACTIVATE, {"email": email.strip().lower()})
    session.commit()


# ── First admin (prestart) ────────────────────────────────────────────────
def ensure_first_admin(session: Session) -> User | None:
    """Create the admin from FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD when no admin
    exists. TOTP is enrolled on that account's first sign-in. Idempotent."""
    existing = session.exec(select(User).where(User.role == "admin")).first()
    if existing is not None:
        return existing
    email = settings.FIRST_ADMIN_EMAIL.strip().lower()
    if not email or not settings.FIRST_ADMIN_PASSWORD:
        log.warning("FIRST_ADMIN_EMAIL / FIRST_ADMIN_PASSWORD not set; no admin seeded")
        return None
    user = create_user(
        session,
        email=email,
        role="admin",
        password=settings.FIRST_ADMIN_PASSWORD,
        display_name="Admin",
    )
    audit(session, "first_admin_seeded", actor_user_id=user.id, target=email)
    log.info("seeded first admin %s", user.id)
    return user
