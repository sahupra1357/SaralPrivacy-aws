"""Transactional email over SMTP (mailcatcher locally, Resend SMTP in production).

`send(...)` returns a provider message id (or a local pseudo id) so callers can log it
to `email_send_log` exactly as the Resend client's id was logged before. Templates are
Jinja2 files under app/email-templates/.
"""

import logging
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings

log = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).resolve().parent.parent / "email-templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html"]))


@dataclass(frozen=True)
class SentEmail:
    message_id: str
    to: list[str]
    subject: str


def render(template: str, **context: object) -> str:
    return _env.get_template(template).render(site_url=settings.NEXT_PUBLIC_SITE_URL, **context)


def send(
    to: str | list[str],
    subject: str,
    html: str,
    *,
    from_: str | None = None,
    reply_to: str | None = None,
    text: str | None = None,
    headers: dict[str, str] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> SentEmail:
    recipients = [to] if isinstance(to, str) else list(to)
    msg = EmailMessage()
    msg["From"] = from_ or settings.EMAILS_FROM_NOREPLY
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="saralprivacy.com")
    if reply_to:
        msg["Reply-To"] = reply_to
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.set_content(text or "This email is best viewed in an HTML-capable client.")
    msg.add_alternative(html, subtype="html")
    for a in attachments or []:
        maintype, _, subtype = str(a.get("content_type", "application/octet-stream")).partition("/")
        content = a["content"]
        if isinstance(content, str):
            content = content.encode("utf-8")
        msg.add_attachment(
            content, maintype=maintype, subtype=subtype or "octet-stream", filename=a["filename"]
        )

    if not settings.EMAILS_ENABLED:
        log.info("email disabled; would send %r to %s", subject, recipients)
        return SentEmail(message_id=f"disabled-{uuid.uuid4()}", to=recipients, subject=subject)

    smtp_cls = smtplib.SMTP_SSL if settings.SMTP_SSL else smtplib.SMTP
    with smtp_cls(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
        if settings.SMTP_TLS and not settings.SMTP_SSL:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
    log.info("email sent %r to %s", subject, recipients)
    return SentEmail(message_id=str(msg["Message-ID"]), to=recipients, subject=subject)
