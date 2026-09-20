"""Settings — the only place environment variables are read.

Reads the repo-root `.env` (one level above backend/) so the same file feeds
Docker Compose and local runs. Every key is documented in `.env.example`.
"""

import secrets
import warnings
from typing import Annotated, Any, Literal, Self

from pydantic import AnyUrl, BeforeValidator, PostgresDsn, computed_field, model_validator
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    if isinstance(v, list | str):
        return v
    raise ValueError(v)


# Generated once per process. A missing SECRET_KEY must be loud in production
# (every session would die on each restart), so the validator below checks for it.
_EPHEMERAL_SECRET = secrets.token_urlsafe(32)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_ignore_empty=True, extra="ignore")

    # ── Identity ──────────────────────────────────────────────────────────
    PROJECT_NAME: str = "SaralPrivacy"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    API_V1_STR: str = "/api/v1"
    FRONTEND_HOST: str = "http://localhost:3000"
    BACKEND_CORS_ORIGINS: Annotated[list[AnyUrl] | str, BeforeValidator(_parse_cors)] = []
    NEXT_PUBLIC_SITE_URL: str = "http://localhost:3000"

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = _EPHEMERAL_SECRET
    TOTP_ENCRYPTION_KEY: str = ""  # Fernet key; generated per process if blank (local only)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours, as today
    PENDING_TOKEN_EXPIRE_MINUTES: int = 10
    CRON_SECRET: str = ""
    EMAIL_LINK_SECRET: str = ""
    CHAT_HISTORY_SECRET: str = ""
    RESEND_WEBHOOK_SECRET: str = ""

    # ── First admin (seeded by prestart when no admin exists) ─────────────
    FIRST_ADMIN_EMAIL: str = "admin@example.com"
    FIRST_ADMIN_PASSWORD: str = "changethis-changethis"
    ADMIN_EMAIL: str = ""  # notification recipient for leads/contact, as today
    EXTRA_ADMIN_EMAILS: str = ""

    # ── Database ──────────────────────────────────────────────────────────
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "saral"
    POSTGRES_PASSWORD: str = "saral"
    POSTGRES_DB: str = "saralprivacy"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> PostgresDsn:
        return PostgresDsn(
            MultiHostUrl.build(
                scheme="postgresql+psycopg",
                username=self.POSTGRES_USER,
                password=self.POSTGRES_PASSWORD,
                host=self.POSTGRES_SERVER,
                port=self.POSTGRES_PORT,
                path=self.POSTGRES_DB,
            ).unicode_string()
        )

    # ── Storage (S3-compatible: MinIO locally, S3 in production) ─────────
    # Empty endpoint/keys mean real AWS S3 with the task role's credentials. Local
    # MinIO values live in .env (env_ignore_empty means an empty env var cannot
    # override a non-empty default, so the defaults themselves must be empty).
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = "saralprivacy"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_REGION: str = "ap-south-1"
    PUBLIC_ASSET_BASE_URL: str = "http://localhost:9000/saralprivacy"
    # Where the DPDPA guide PDFs are served from. Blank = the frontend's own
    # /guides/pdf (the files sit in frontend/public). Set to an R2/S3 public base
    # when they are hosted outside the app — the URL goes into emails and WhatsApp.
    GUIDE_PDF_BASE_URL: str = ""

    # ── Email (SMTP; mailcatcher locally, Resend SMTP in production) ──────
    SMTP_HOST: str = "mailcatcher"
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TLS: bool = False
    SMTP_SSL: bool = False
    EMAILS_FROM_NOREPLY: str = "SaralPrivacy <noreply@saralprivacy.com>"
    EMAILS_FROM_BRIEFINGS: str = "SaralPrivacy Briefings <briefings@saralprivacy.com>"
    EMAILS_ENABLED: bool = True

    # ── External APIs ─────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"
    PINECONE_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    GITHUB_TOKEN: str = ""
    GITHUB_OWNER: str = ""
    GITHUB_REPO: str = ""
    GSC_SERVICE_ACCOUNT_JSON: str = ""
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_WHATSAPP_FROM: str = ""

    # ── Editorial pipeline (daily briefing) ───────────────────────────────
    BRIEFING_CRON_SECRET: str = ""
    GOOGLE_SHEET_ID: str = ""
    GOOGLE_CREDENTIALS_JSON: str = ""
    GOOGLE_CREDENTIALS_PATH: str = ""
    ROADMAP_CSV_PATH: str = ""
    SERP_API_KEY: str = ""
    KIE_API_KEY: str = ""
    NANO_BANANA_MODEL: str = "nano-banana-2"
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_MAX_TOKENS: int = 4096
    CLAUDE_TEMPERATURE: float = 0.3

    # ── Behaviour knobs carried over ──────────────────────────────────────
    OUTREACH_DAILY_CAP: int = 50
    TZ: str = "Asia/Kolkata"

    # ── Validation ────────────────────────────────────────────────────────
    def _check_default(self, name: str, value: str, bad: str) -> None:
        if value == bad:
            msg = f'{name} is still the default "{bad}" — set it in .env'
            if self.ENVIRONMENT == "local":
                warnings.warn(msg, stacklevel=1)
            else:
                raise ValueError(msg)

    @model_validator(mode="after")
    def _enforce_secrets(self) -> Self:
        self._check_default("SECRET_KEY", self.SECRET_KEY, _EPHEMERAL_SECRET)
        self._check_default(
            "FIRST_ADMIN_PASSWORD", self.FIRST_ADMIN_PASSWORD, "changethis-changethis"
        )
        self._check_default("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD, "saral")
        if self.ENVIRONMENT != "local" and not self.TOTP_ENCRYPTION_KEY:
            raise ValueError("TOTP_ENCRYPTION_KEY must be set outside local")
        return self

    @property
    def all_cors_origins(self) -> list[str]:
        origins = [str(o).rstrip("/") for o in self.BACKEND_CORS_ORIGINS]
        return [*origins, self.FRONTEND_HOST.rstrip("/")]


settings = Settings()
