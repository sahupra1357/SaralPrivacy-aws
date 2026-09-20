"""Auth tables — schema `app`.

Replaces Supabase Auth (`auth.users`, its MFA factors and its rate limiter) with five
plain tables we own:

    users           identity, bcrypt password, role, Fernet-encrypted TOTP secret
    sessions        one row per signed-in session; the access token's `jti` is its id,
                    so revoking a row logs that token out immediately
    login_attempts  fixed-window counters behind core/ratelimit.py (created in
                    migration 0001; modelled here so autogenerate keeps it)
    audit_log       who did what, append-only
    invite_tokens   single-use invite / recovery links (sha256 of the emailed token)

`ops.blogger_accounts` keeps the `active` flag the admin UI toggles and login checks.
It is *not* modelled here — the admin module owns that table; `crud.auth` reads it with
a narrow SQL statement so the two modules never map the same table twice.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Column, DateTime, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

Role = Literal["admin", "blogger"]
TokenPurpose = Literal["invite", "recovery"]

ROLES: tuple[str, ...] = ("admin", "blogger")
TOKEN_PURPOSES: tuple[str, ...] = ("invite", "recovery")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid_pk() -> Any:
    return Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("ops.uuid_generate_v7()")},
    )


def _ts(*, nullable: bool = False, default: bool = True) -> Any:
    """A timestamptz column. Each Field needs its own Column instance."""
    column = Column(DateTime(timezone=True), nullable=nullable)
    if default:
        return Field(default_factory=utcnow, sa_column=column)
    return Field(default=None, sa_column=column)


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = {"schema": "app"}

    id: uuid.UUID = _uuid_pk()
    email: str = Field(index=True, unique=True, max_length=320)
    hashed_password: str = Field(max_length=255)
    role: str = Field(default="admin", max_length=16)
    is_active: bool = Field(default=True)
    display_name: str | None = Field(default=None, max_length=255)
    # Fernet ciphertext of the base32 TOTP secret; never the secret itself.
    totp_secret_enc: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    totp_confirmed: bool = Field(default=False)
    # Replay guard: the last accepted TOTP time step. A code at or below it is refused
    # even while it is still inside the drift window.
    last_totp_step: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    created_at: datetime = _ts()
    updated_at: datetime = _ts()


class AuthSession(SQLModel, table=True):
    """One signed-in session. `id` is the access token's `jti` claim."""

    __tablename__ = "sessions"
    __table_args__ = {"schema": "app"}

    id: uuid.UUID = _uuid_pk()
    user_id: uuid.UUID = Field(foreign_key="app.users.id", index=True)
    issued_at: datetime = _ts()
    expires_at: datetime = _ts()
    revoked_at: datetime | None = _ts(nullable=True, default=False)
    ip: str | None = Field(default=None, max_length=64)
    user_agent: str | None = Field(default=None, sa_column=Column(Text, nullable=True))


class LoginAttempt(SQLModel, table=True):
    """Backs `core/ratelimit.py` for the whole app. Created in migration 0001."""

    __tablename__ = "login_attempts"
    __table_args__ = {"schema": "app"}

    key: str = Field(sa_column=Column(Text, primary_key=True))
    window_start: datetime = _ts()
    count: int = Field(default=0)


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "app"}

    id: uuid.UUID = _uuid_pk()
    actor_user_id: uuid.UUID | None = Field(default=None, index=True)
    action: str = Field(max_length=64)
    target: str | None = Field(default=None, max_length=255)
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB, nullable=True))
    at: datetime = _ts()


class InviteToken(SQLModel, table=True):
    """Single-use invite / recovery link. Only the sha256 of the emailed token is stored."""

    __tablename__ = "invite_tokens"
    __table_args__ = {"schema": "app"}

    id: uuid.UUID = _uuid_pk()
    user_id: uuid.UUID = Field(foreign_key="app.users.id", index=True)
    purpose: str = Field(max_length=16)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    expires_at: datetime = _ts()
    used_at: datetime | None = _ts(nullable=True, default=False)
    created_at: datetime = _ts()
