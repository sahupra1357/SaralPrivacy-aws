import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, text
from sqlmodel import Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """id / legacy_id / created_at / updated_at exactly as the Supabase DDL defined them."""

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        sa_column_kwargs={"server_default": text("ops.uuid_generate_v7()")},
    )
    legacy_id: str | None = Field(default=None, unique=True, index=False)
    # sa_type + sa_column_kwargs (not sa_column): a Column object can attach to ONE
    # table only, so a shared sa_column would break the second model using the mixin.
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": text("now()")},
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"server_default": text("now()")},
    )
