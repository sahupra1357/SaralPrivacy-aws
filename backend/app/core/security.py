"""Password hashing, JWTs (pending + access), TOTP secret encryption.

Policy (auth-module skill): bcrypt passwords; HS256 JWTs signed with SECRET_KEY;
a 10-minute *pending* token carries only `sub` + purpose "mfa"; the 8-hour *access*
token carries `sub`, `role`, `name`, `jti` (= sessions.id). TOTP secrets are stored
Fernet-encrypted with TOTP_ENCRYPTION_KEY.
"""

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt
import pyotp
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

ALGORITHM = "HS256"
Role = Literal["admin", "blogger"]

TOTP_ISSUER = "SaralPrivacy"
TOTP_INTERVAL = 30
TOTP_DRIFT_STEPS = 1  # pyotp valid_window — one step either side, as Supabase allowed

# bcrypt only reads the first 72 bytes of a password. Truncate explicitly so behaviour
# matches passlib (which this replaced) and bcrypt>=5, which rejects longer input.
_BCRYPT_MAX_BYTES = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


MIN_PASSWORD = 12
MAX_PASSWORD = 128


# ── Passwords ─────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(plain), hashed.encode("ascii"))
    except ValueError:  # malformed or non-bcrypt hash
        return False


def password_ok(password: str) -> bool:
    return MIN_PASSWORD <= len(password) <= MAX_PASSWORD


# ── Tokens ────────────────────────────────────────────────────────────────
def _encode(claims: dict[str, Any], minutes: int) -> str:
    now = datetime.now(UTC)
    payload = {**claims, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_pending_token(user_id: uuid.UUID) -> str:
    return _encode({"sub": str(user_id), "purpose": "mfa"}, settings.PENDING_TOKEN_EXPIRE_MINUTES)


def create_access_token(
    user_id: uuid.UUID, role: Role, session_id: uuid.UUID, name: str | None = None
) -> str:
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "jti": str(session_id),
        "purpose": "access",
    }
    if name:
        claims["name"] = name
    return _encode(claims, settings.ACCESS_TOKEN_EXPIRE_MINUTES)


def decode_token(token: str, purpose: Literal["mfa", "access"]) -> dict[str, Any] | None:
    """Verified claims, or None for any signature/expiry/purpose failure."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != purpose or "sub" not in payload:
        return None
    return dict(payload)


# ── One-time tokens (invite / recovery / email links) ─────────────────────
def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_opaque_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── TOTP secret at rest ───────────────────────────────────────────────────
def _fernet() -> Fernet:
    key = settings.TOTP_ENCRYPTION_KEY
    if not key:
        # Local-only fallback derived from SECRET_KEY so dev works without extra setup.
        key = base64.urlsafe_b64encode(
            hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        ).decode()
    return Fernet(key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None


# ── TOTP ──────────────────────────────────────────────────────────────────
def new_totp_secret() -> str:
    """A fresh base32 secret. Stored only Fernet-encrypted (`encrypt_secret`)."""
    return str(pyotp.random_base32())


def totp_uri(secret: str, account: str) -> str:
    """The otpauth:// URI an authenticator app scans."""
    return str(
        pyotp.TOTP(secret, interval=TOTP_INTERVAL).provisioning_uri(
            name=account, issuer_name=TOTP_ISSUER
        )
    )


def totp_step(at: float | None = None) -> int:
    """The 30-second time step a timestamp falls in."""
    now = at if at is not None else datetime.now(UTC).timestamp()
    return int(now // TOTP_INTERVAL)


def verify_totp(secret: str, code: str, last_step: int | None = None) -> int | None:
    """Return the time step the code belongs to, or None when it does not verify.

    A code is refused when its step is at or below `last_step`, so a code that is
    still inside the drift window cannot be replayed after it has been used once.
    """
    if not code or not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL)
    now = datetime.now(UTC).timestamp()
    current = totp_step(now)
    for offset in range(-TOTP_DRIFT_STEPS, TOTP_DRIFT_STEPS + 1):
        step = current + offset
        if secrets.compare_digest(totp.at(step * TOTP_INTERVAL), code):
            if last_step is not None and step <= last_step:
                return None
            return step
    return None
