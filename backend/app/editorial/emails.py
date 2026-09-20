"""Briefing emails.

- The admin "send to subscribers" approval mail (`lib/email.ts` sendBriefingApprovalEmail)
  lives here.
- The subscriber broadcast and its audience (`sendBriefingToSubscribers`,
  `lib/sendGateway.ts`) are owned by the outreach module, which also sends the daily
  briefing cron; approve/send reuse `app.jobs.outreach` so there is one template and one
  audience rule.
"""

import logging
from dataclasses import dataclass

from sqlmodel import Session

from app.core.config import settings
from app.editorial import config
from app.editorial.docs import briefing_doc, parse_json
from app.models.editorial import Briefing
from app.services import email

log = logging.getLogger(__name__)

# All admins receive approval emails; any one click approves for all (lib/email.ts).
DEFAULT_ADMIN_EMAILS = (
    "dilip.sahu@gmail.com",
    "sahudilip1356@gmail.com",
    "saralprivacy@gmail.com",
)


@dataclass(frozen=True)
class BroadcastResult:
    sent: int
    failed: int
    total: int


def _checklist(raw: str | None) -> list[str]:
    items = parse_json(raw or "[]", [])
    return [str(i) for i in items] if isinstance(items, list) else []


def approval_link(briefing: Briefing, token: str) -> str:
    return f"{config.site_url()}/api/briefings/approve?token={token}&briefingId={briefing.id}"


def send_approval_email(briefing: Briefing, token: str) -> bool:
    """Returns False only when every admin send failed (lib/email.ts semantics)."""
    subject = f"New Briefing LIVE: {briefing.title} — Send to Subscribers"
    html = email.render(
        "editorial/briefing_approval.html",
        title=briefing.title,
        summary=briefing.summary,
        why_it_matters=briefing.why_it_matters,
        checklist=_checklist(briefing.action_checklist),
        scheduled_for=briefing.scheduled_for.isoformat() if briefing.scheduled_for else None,
        approve_link=approval_link(briefing, token),
    )
    recipients = [*DEFAULT_ADMIN_EMAILS, *config.extra_admin_emails()]
    failed = 0
    for addr in recipients:
        try:
            email.send(addr, subject, html, from_=settings.EMAILS_FROM_NOREPLY)
        except Exception as e:  # noqa: BLE001 — one admin failing must not stop the others
            failed += 1
            log.warning("approval email to an admin failed: %s", e)
    return failed < len(recipients)


def broadcast(session: Session, briefing: Briefing) -> BroadcastResult:
    """Send the briefing to every eligible subscriber (outreach's gateway + template)."""
    from app.jobs import outreach  # noqa: PLC0415 — sibling module, imported at use

    subscribers = outreach.eligible_subscribers(session)
    payload = briefing_doc(briefing)
    payload["status"] = "approved"
    sent, failed = outreach.send_briefing_to_subscribers(payload, subscribers)
    return BroadcastResult(sent=sent, failed=failed, total=len(subscribers))
