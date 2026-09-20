"""Tables written by the Setu chat widget.

Only one belongs to this module: `ops.chat_feedback` (decision D2 — redacted, failures
only). The handoff route also writes `ops.leads` and `ops.consent_log`, but those tables
belong to the **forms** module (`app.models.forms.Lead` / `ConsentLog`); chat inserts into
them with a plain parameterised statement in `app.crud.chat` rather than importing another
module's models. See docs/build/status/chat.md.

Column names mirror `_backup/supabase/migrations/0001_initial_schema.sql` exactly, camelCase
quoting included, so the data export for `make seed` loads without a rename pass.
"""

from datetime import datetime

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin

FAILURE_KINDS = ("refusal", "low_confidence", "thumbs_down")


class ChatFeedback(TimestampMixin, SQLModel, table=True):
    """👍/👎 plus failure-turn logging.

    `redacted_question` is written ONLY when `failure_kind` is set, and only after
    `redact_text()` — successful turns store the boolean signal alone (D2).
    """

    __tablename__ = "chat_feedback"
    __table_args__ = {"schema": "ops"}

    session_id: str = Field(nullable=False, sa_column_kwargs={"name": "sessionId"})
    turn_id: str = Field(nullable=False, sa_column_kwargs={"name": "turnId"})
    helpful: bool | None = Field(default=None, nullable=True)
    reason: str | None = Field(default=None, nullable=True)
    page_url: str | None = Field(default=None, nullable=True, sa_column_kwargs={"name": "pageUrl"})
    failure_kind: str | None = Field(
        default=None, nullable=True, sa_column_kwargs={"name": "failureKind"}
    )
    redacted_question: str | None = Field(
        default=None, nullable=True, sa_column_kwargs={"name": "redactedQuestion"}
    )
    ts: datetime | None = Field(default=None, sa_type=DateTime(timezone=True), nullable=True)
