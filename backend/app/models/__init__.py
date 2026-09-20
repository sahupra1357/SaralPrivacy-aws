"""Import every module's models here so Alembic autogenerate and SQLModel see them.
Orchestrator only: one import line per module as it is integrated.
"""

from app.models.admin import (  # noqa: F401
    AdminTask,
    AiCitation,
    BloggerAccount,
    SeoIndexRequest,
    SeoInspection,
    SeoRun,
)
from app.models.assessments import Assessment  # noqa: F401
from app.models.auth import AuditLog, AuthSession, InviteToken, LoginAttempt, User  # noqa: F401
from app.models.base import TimestampMixin
from app.models.chat import ChatFeedback  # noqa: F401
from app.models.editorial import BlogPost, Briefing  # noqa: F401
from app.models.forms import (  # noqa: F401
    ConsentLog,
    Download,
    Lead,
    Subscriber,
    SurveyResponse,
    TemplateDownload,
)
from app.models.notices import NoticeCapture, NoticeEvent  # noqa: F401
from app.models.outreach import EmailSendLog, OutreachContact  # noqa: F401

__all__ = [
    "AdminTask",
    "AiCitation",
    "BlogPost",
    "BloggerAccount",
    "Briefing",
    "SeoIndexRequest",
    "SeoInspection",
    "SeoRun",
    "Assessment",
    "ConsentLog",
    "Download",
    "EmailSendLog",
    "Download",
    "Lead",
    "Subscriber",
    "SurveyResponse",
    "TemplateDownload",
    "AuditLog",
    "AuthSession",
    "ChatFeedback",
    "InviteToken",
    "LoginAttempt",
    "NoticeCapture",
    "NoticeEvent",
    "OutreachContact",
    "TimestampMixin",
    "User",
]
