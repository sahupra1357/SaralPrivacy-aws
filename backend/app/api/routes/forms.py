"""Public lead-capture forms: contact, subscribe, unsubscribe, survey, template
downloads and the DPDPA Guide (white paper) gate.

Ported from the seven Next.js route handlers listed in docs/build/inventory/forms.md.
Every user-facing string, status code and guard order is preserved:

- honeypot `hp_url` → pretend success, store nothing;
- rate limits keep the original key, count and window (and the original position
  relative to the honeypot check, which differs between routes);
- writes the TypeScript fired and forgot stay non-fatal here too.

No authentication: all seven are public.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import phonenumbers
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.api.deps import RateLimit, SessionDep, client_geo, client_ip
from app.core.config import settings
from app.crud import forms as crud
from app.models.forms import Download, Lead, Subscriber, SurveyResponse, TemplateDownload
from app.services import email as email_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/forms", tags=["forms"])

PRIVACY_NOTICE_VERSION = crud.PRIVACY_NOTICE_VERSION
HONEYPOT_FIELD = "hp_url"  # lib/abuseGuard.ts

# ── Template catalogue (lib/templates/validation.ts) ──────────────────────
TEMPLATE_OPTIONS = (
    "privacy-notice",
    "consent-language-examples",
    "data-inventory-register",
    "dsr-grievance-sop",
    "vendor-data-sharing-register",
)
TEMPLATE_NAMES = {
    "privacy-notice": "Privacy Notice Template",
    "consent-language-examples": "Consent Language Examples",
    "data-inventory-register": "Data Inventory Register",
    "dsr-grievance-sop": "DSR & Grievance SOP",
    "vendor-data-sharing-register": "Vendor Data Sharing Register",
}
TEMPLATE_EXTENSIONS = {
    "privacy-notice": ".docx",
    "consent-language-examples": ".docx",
    "data-inventory-register": ".xlsx",
    "dsr-grievance-sop": ".docx",
    "vendor-data-sharing-register": ".xlsx",
}

# ── DPDPA Guide PDFs (lib/data/guide-languages.ts) ────────────────────────
# Mirror of the `code → pdfUrl` half of GUIDE_LANGUAGES. The guide PDFs are large
# binaries kept out of git and are still served from their published URLs; only the
# *templates* above moved into frontend/public.
DEFAULT_LANG_CODE = "en"
# Self-hosted guide PDFs (frontend/public/guides/pdf/). Absolute because the URL
# goes into emails.
_GUIDE_BLOB = f"{settings.NEXT_PUBLIC_SITE_URL.rstrip('/')}/guides/pdf"
GUIDE_PDF_URLS: dict[str, str | None] = {
    "en": f"{_GUIDE_BLOB}/dpdpa-guide-en.pdf",
    "hi": f"{_GUIDE_BLOB}/dpdpa-guide-hi.pdf",
    "gu": f"{_GUIDE_BLOB}/dpdpa-guide-gu.pdf",
    "mr": f"{_GUIDE_BLOB}/dpdpa-guide-mr.pdf",
    "kn": f"{_GUIDE_BLOB}/dpdpa-guide-kn.pdf",
    "ta": f"{_GUIDE_BLOB}/dpdpa-guide-ta.pdf",
    "te": f"{_GUIDE_BLOB}/dpdpa-guide-te.pdf",
}

# ── Survey report presentation (lib/email-templates.ts) ───────────────────
BAND_COLOR = {
    "Not Started": "#DC2626",
    "Early Stage": "#F97316",
    "Building Foundations": "#EAB308",
    "Progressing Well": "#22C55E",
    "Operationally Strong": "#16A34A",
}
BAND_CTA: dict[str, dict[str, str]] = {
    "Not Started": {
        "headline": "Start here — What is DPDPA? (Plain English guide)",
        "body": "You are at the beginning of your DPDPA journey. This guide explains what the law requires in plain language — no legal jargon, just the essentials for your business.",
        "href": "https://saralprivacy.com/learn/what-is-dpdpa",
        "label": "Read the Plain English Guide →",
    },
    "Early Stage": {
        "headline": "Download your free DPDPA Readiness Checklist",
        "body": "You have some awareness but important gaps remain. This checklist shows the 5 most common mistakes Indian businesses make and simple ways to fix them this week.",
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Building Foundations": {
        "headline": "Download your free DPDPA Readiness Checklist",
        "body": "Good progress. Use this checklist to find remaining gaps and close them systematically. Focus on consistency across all your data channels and vendor agreements.",
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Progressing Well": {
        "headline": "Tighten your vendor agreements with our DPA template",
        "body": "You are ahead of most businesses. The next step is securing your vendor chain. This template gives you the exact data processing clauses to add to your contracts.",
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Operationally Strong": {
        "headline": "Book a review session to certify your controls",
        "body": "Strong readiness signals across all areas. A structured review with our experts will confirm your controls are audit-ready and identify any remaining edge-case gaps.",
        "href": "https://saralprivacy.com/contact",
        "label": "Book Expert Review →",
    },
}

RATE_LIMIT_WAIT = "Too many requests. Please wait a moment and try again."
RATE_LIMIT_LATER = "Too many requests. Please try again later."
RATE_LIMIT_UNSUB = "Too many requests. Try again later or email privacy@saralprivacy.com."


# ── Shared helpers ────────────────────────────────────────────────────────
class _Meta(BaseModel):
    ip: str = ""
    city: str = ""
    country: str = ""
    region: str = ""
    user_agent: str = ""


def request_meta(request: Request) -> _Meta:
    """IP / coarse geo / user agent, captured at the moment of consent for the audit
    trail (disclosed in the Privacy Notice). Geo headers are Vercel's; absent behind
    any other proxy, in which case the fields stay empty exactly as before."""
    return _Meta(
        ip=client_ip(request),
        city=client_geo(request).city,
        country=client_geo(request).country,
        region=client_geo(request).region,
        user_agent=request.headers.get("user-agent", ""),
    )


def honeypot_tripped(value: str | None) -> bool:
    """Only bots fill the hidden field."""
    return bool(value and value.strip())


def now_utc() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    """`new Date().toISOString()` — UTC with a trailing Z and milliseconds."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def admin_email() -> str:
    return settings.ADMIN_EMAIL or "dilip.sahu@gmail.com"


def _log_consent_safely(session: Session, **kwargs: Any) -> None:
    """The TypeScript wrote consent rows with `.catch(console.error)` — never fatal."""
    try:
        crud.log_consent(session, **kwargs)
    except Exception:
        session.rollback()
        log.exception("consent_log write error")


def _upsert_subscriber_safely(session: Session, **kwargs: Any) -> None:
    try:
        crud.upsert_subscriber(session, **kwargs)
    except Exception:
        session.rollback()
        log.exception("upsertSubscriber failed")


def _send_safely(
    to: str, subject: str, html: str, *, from_: str | None = None, **kwargs: Any
) -> None:
    """Fire-and-forget email: logged on failure, never surfaced to the caller."""
    try:
        email_service.send(to, subject, html, from_=from_, **kwargs)
    except TypeError:
        # The core email service may not support this keyword yet (attachments).
        # Send without it rather than drop the mail; see docs/build/status/forms.md.
        log.warning("email service rejected %s; retrying plain", sorted(kwargs))
        try:
            email_service.send(to, subject, html, from_=from_)
        except Exception:
            log.exception("email send failed: %s", subject)
    except Exception:
        log.exception("email send failed: %s", subject)


# ── 1. POST /forms/contact ────────────────────────────────────────────────
class ContactIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    full_name: str | None = Field(default=None, alias="fullName")
    work_email: str | None = Field(default=None, alias="workEmail")
    mobile_number: str | None = Field(default=None, alias="mobileNumber")
    company_name: str | None = Field(default=None, alias="companyName")
    industry: str | None = None
    company_size: str | None = Field(default=None, alias="companySize")
    issue_summary: str | None = Field(default=None, alias="issueSummary")
    preferred_contact: str | None = Field(default=None, alias="preferredContact")
    preferred_time: str | None = Field(default=None, alias="preferredTime")
    consent_contact: bool | None = Field(default=None, alias="consentContact")
    hp_url: str | None = None


class SuccessOut(BaseModel):
    success: bool = True
    message: str | None = None


@router.post("/contact", response_model=SuccessOut, response_model_exclude_none=True)
def submit_contact(
    body: ContactIn,
    request: Request,
    response: Response,
    session: SessionDep,
    background: BackgroundTasks,
) -> Any:
    RateLimit("contact", 6, 60, message=RATE_LIMIT_WAIT)(request, response, session)

    if honeypot_tripped(body.hp_url):
        return SuccessOut(message="Your request has been received.")

    if not (
        body.full_name
        and body.work_email
        and body.company_name
        and body.issue_summary
        and body.consent_contact
    ):
        return JSONResponse({"detail": "Required fields are missing."}, status_code=400)

    meta = request_meta(request)
    now = now_utc()
    lead = Lead(
        name=body.full_name,
        email=body.work_email,
        phone=body.mobile_number or "",
        company=body.company_name,
        industry=body.industry or "",
        company_size=body.company_size or "",
        source="consultation",
        issue_summary=body.issue_summary,
        preferred_contact=body.preferred_contact or "",
        preferred_time=body.preferred_time or "",
        consent_version=PRIVACY_NOTICE_VERSION,
        risk_level="",
        created_at_attr=now,
        ip_address=meta.ip,
        city=meta.city,
        country=meta.country,
        region=meta.region,
    )
    crud.create_lead(session, lead)

    _log_consent_safely(
        session,
        email=body.work_email,
        name=body.full_name,
        source="contact",
        consent_type="data_processing",
        timestamp=iso(now),
        ip_address=meta.ip,
        user_agent=meta.user_agent,
        city=meta.city,
        country=meta.country,
        region=meta.region,
    )

    html = email_service.render("forms/consultation_alert.html", lead=lead)
    background.add_task(
        _send_safely,
        admin_email(),
        f"New Consultation Request — {lead.name} from {lead.company}",
        html,
        from_=settings.EMAILS_FROM_NOREPLY,
    )

    return SuccessOut(
        message="Your consultation request has been received. We will respond within one business day."
    )


# ── 2. POST /forms/subscribe ──────────────────────────────────────────────
class SubscribeIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str | None = None
    email: str | None = None
    industry: str | None = None
    frequency: str | None = None
    consent_email: bool | None = Field(default=None, alias="consentEmail")
    hp_url: str | None = None


def _looks_like_email(value: str) -> bool:
    """`/\\S+@\\S+\\.\\S+/` — the same shallow shape check the form used."""
    local, _, domain = value.partition("@")
    return bool(local and " " not in local and "." in domain and " " not in domain)


@router.post("/subscribe", response_model=SuccessOut, response_model_exclude_none=True)
def subscribe(
    body: SubscribeIn,
    request: Request,
    response: Response,
    session: SessionDep,
    background: BackgroundTasks,
) -> Any:
    RateLimit("subscribe", 6, 60, message=RATE_LIMIT_WAIT)(request, response, session)

    if honeypot_tripped(body.hp_url):
        return SuccessOut(message="Subscription successful.")

    if not (body.email and body.consent_email):
        return JSONResponse({"detail": "Email and email consent are required."}, status_code=400)
    if not _looks_like_email(body.email):
        return JSONResponse({"detail": "Invalid email address."}, status_code=400)

    meta = request_meta(request)
    now = now_utc()

    # Dedupe: re-subscribing after an unsubscribe flips the existing row back to active
    # instead of adding a second one (duplicate rows meant duplicate briefing emails).
    existing = crud.get_subscriber_by_email(session, body.email)
    if existing is not None:
        if existing.status != "active":
            crud.reactivate_subscriber(session, existing)
    else:
        crud.create_subscriber(
            session,
            Subscriber(
                name=(body.name or "").strip() or body.email.split("@")[0],
                email=body.email,
                industry=body.industry or "",
                frequency=body.frequency or "daily",
                consent_version=PRIVACY_NOTICE_VERSION,
                consent_source="manual",
                status="active",
                user_agent=meta.user_agent,
                created_at_attr=now,
                ip_address=meta.ip,
                city=meta.city,
                country=meta.country,
                region=meta.region,
            ),
        )

    _log_consent_safely(
        session,
        email=body.email,
        name=body.name,
        source="subscribe",
        consent_type="email_marketing",
        timestamp=iso(now),
        ip_address=meta.ip,
        user_agent=meta.user_agent,
        city=meta.city,
        country=meta.country,
        region=meta.region,
    )

    html = email_service.render("forms/welcome.html", name=body.name or "")
    background.add_task(
        _send_safely,
        body.email,
        "Welcome to SaralPrivacy Daily Briefings",
        html,
        from_=settings.EMAILS_FROM_BRIEFINGS,
    )

    return SuccessOut(message="Subscription successful. Check your inbox for confirmation.")


# ── 3. POST /forms/subscribers/unsubscribe ────────────────────────────────
class UnsubscribeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str | None = None
    sig: str | None = None


class UnsubscribeOut(BaseModel):
    success: bool = True
    already_removed: bool | None = None


def verify_unsubscribe_sig(email: str, sig: str | None) -> bool:
    """lib/sendGateway.ts: hex HMAC-SHA256 of the normalised email under
    EMAIL_LINK_SECRET. Without the secret, links go out unsigned and this is always
    false — the unsigned path is throttled, never blocked."""
    secret = (settings.EMAIL_LINK_SECRET or "").strip()
    if not sig or not secret:
        return False
    expected = hmac.new(secret.encode(), email.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@router.post(
    "/subscribers/unsubscribe", response_model=UnsubscribeOut, response_model_exclude_none=True
)
def unsubscribe(
    body: UnsubscribeIn, request: Request, response: Response, session: SessionDep
) -> Any:
    if not body.email:
        return JSONResponse({"detail": "email required"}, status_code=400)

    normalised = crud.normalise_email(body.email)

    # Links in our emails carry an HMAC sig and are honoured unconditionally. Unsigned
    # requests (old emails, the consent-preferences form) still work — unsubscribes must
    # never be blocked outright — but are throttled so a griefer can't bulk-suppress the
    # subscriber list by posting raw emails.
    if not verify_unsubscribe_sig(normalised, body.sig):
        RateLimit("unsub", 5, 60 * 60, message=RATE_LIMIT_UNSUB)(request, response, session)

    existing = crud.get_subscriber_by_email(session, normalised)
    if existing is None:
        # Not found — still return success so the page shows a clean state.
        return UnsubscribeOut(already_removed=True)

    crud.unsubscribe(session, existing, now_utc())
    return UnsubscribeOut()


# ── 4. POST /forms/survey/submit ──────────────────────────────────────────
class SurveySubmitIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    hp_url: str | None = None


def _json_list(value: Any) -> str:
    return json.dumps(value if isinstance(value, list) else [], separators=(",", ":"))


def render_survey_report(data: dict[str, Any]) -> tuple[str, str]:
    """surveyResultEmailTemplate: returns (subject, html)."""
    band = str(data.get("band") or "")
    score = int(data.get("score") or 0)
    band_color = BAND_COLOR.get(band, "#E07B39")
    cta = BAND_CTA.get(band, BAND_CTA["Early Stage"])

    if band in ("Not Started", "Early Stage"):
        subject = f"Your DPDPA Score: {score}/100 — Here's exactly why and what to do first"
    elif band == "Operationally Strong":
        subject = f"Your DPDPA Score: {score}/100 — Strong start. Here's what to protect"
    else:
        subject = f"Your DPDPA Score: {score}/100 — You're building. Here's the path to 70+"

    cats = data.get("category_scores") or None
    scorecards: list[dict[str, Any]] = []
    if cats:
        data_inventory = round((cats["retention_deletion"] + cats["vendor_partner_risk"]) / 2)
        pairs = [
            ("Notice & Consent", cats["notice_consent"]),
            ("Data Inventory & Storage", data_inventory),
            ("Data Principal Rights & Control", cats["access_control"]),
            ("Ownership & Governance", cats["ownership_governance"]),
            ("Incident & Operational Readiness", cats["incident_readiness"]),
        ]
        for label, raw in pairs:
            pct = min(100, max(0, int(raw)))
            if pct >= 70:
                status_label, color = "Strong", "#16A34A"
            elif pct >= 40:
                status_label, color = "Developing", "#E07B39"
            else:
                status_label, color = "Needs Work", "#DC2626"
            scorecards.append(
                {
                    "label": label,
                    "score": raw,
                    "filled": round(pct * 2.36),
                    "color": color,
                    "status": status_label,
                }
            )

    token = data.get("report_token")
    html = email_service.render(
        "forms/survey_result.html",
        greeting=f"Hi {data['name']}" if data.get("name") else "Hi there",
        business_name=data.get("business_name") or "",
        score=score,
        band=band,
        band_color=band_color,
        bar_width=max(4, round(score * 5.36)),
        summary=data.get("summary") or "",
        risk_flags=data.get("risk_flags") or [],
        scorecards=scorecards,
        answers=(data.get("answer_summary") or [])[:3],
        recommendations=data.get("recommendations") or [],
        checklist_url=data.get("checklist_url"),
        checklist_title=data.get("checklist_title"),
        cta=cta,
        report_url=(
            f"https://saralprivacy.com/report/{token}"
            if token
            else "https://saralprivacy.com/assessment"
        ),
    )
    return subject, html


@router.post("/survey/submit", response_model=SuccessOut, response_model_exclude_none=True)
def submit_survey(
    body: SurveySubmitIn, request: Request, response: Response, session: SessionDep
) -> Any:
    RateLimit("survey", 8, 60, message=RATE_LIMIT_WAIT)(request, response, session)

    if honeypot_tripped(body.hp_url):
        return SuccessOut()

    if not body.answers or not body.score:
        return JSONResponse({"detail": "Missing answers or score"}, status_code=400)

    answers, score = body.answers, body.score
    wants_report = answers.get("want_detailed_report") == "Yes, send it to me"
    now = iso(now_utc())

    # Non-blocking: a failed save is logged but does not fail the response.
    try:
        crud.create_survey_response(
            session,
            SurveyResponse(
                role=answers.get("role") or "",
                employee_size=answers.get("employee_size") or "",
                sector=answers.get("sector") or "",
                operating_footprint=answers.get("operating_footprint") or "",
                state_ut=answers.get("state_ut") or "",
                city=answers.get("city") or "",
                digital_personal_data=answers.get("digital_personal_data") or "",
                data_types=_json_list(answers.get("data_types")),
                data_storage=_json_list(answers.get("data_storage")),
                controls_in_place=_json_list(answers.get("controls_in_place")),
                readiness_self_view=answers.get("readiness_self_view") or "",
                biggest_blocker=answers.get("biggest_blocker") or "",
                most_helpful_resource=answers.get("most_helpful_resource") or "",
                score=score.get("score"),
                score_band=score.get("band"),
                wants_report=wants_report,
                name=answers.get("name") or "",
                business_name=answers.get("business_name") or "",
                work_email=answers.get("work_email") or "",
                mobile_number=answers.get("mobile_number") or "",
                contact_preference=answers.get("contact_preference") or "",
                consent_followup=bool(answers.get("consent_followup") or False),
                consent_given=True,
                consent_version="v1.0",
                consent_timestamp=now,
                created_at_attr=now,
            ),
        )
    except Exception:
        session.rollback()
        log.exception("[survey] save failed")

    if wants_report and answers.get("work_email"):
        try:
            subject, html = render_survey_report(
                {
                    "name": answers.get("name") or "there",
                    "business_name": answers.get("business_name") or "",
                    "score": score.get("score"),
                    "band": score.get("band"),
                    "summary": score.get("summary"),
                    "recommendations": score.get("recommendations") or [],
                    "risk_flags": score.get("riskFlags") or [],
                    "answer_summary": [],
                }
            )
            email_service.send(
                answers["work_email"], subject, html, from_=settings.EMAILS_FROM_BRIEFINGS
            )
        except Exception:
            log.exception("[survey] Follow-up email failed")

    return SuccessOut()


# ── 5. POST /forms/template-download ──────────────────────────────────────
class TemplateDownloadIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    business_name: str | None = Field(default=None, alias="businessName")
    employees: str | None = None
    contact_name: str | None = Field(default=None, alias="contactName")
    phone: str | None = None
    consent_contact: bool | None = Field(default=None, alias="consentContact")
    consent_briefings: bool | None = Field(default=None, alias="consentBriefings")
    template_name: str | None = Field(default=None, alias="templateName")
    report_token: str | None = Field(default=None, alias="reportToken")
    email: str | None = None
    source: str | None = None
    inventory_csv: str | None = Field(default=None, alias="inventoryCsv")
    niche_name: str | None = Field(default=None, alias="nicheName")
    hp_url: str | None = None


DISCOVERY_CSV_FILENAME = "dpdpa-personal-data-inventory.csv"


@router.post("/template-download", response_model=SuccessOut, response_model_exclude_none=True)
def template_download(
    body: TemplateDownloadIn,
    request: Request,
    response: Response,
    session: SessionDep,
    background: BackgroundTasks,
) -> Any:
    # Rate-limit per IP (also hardens the existing template downloads).
    RateLimit("tmpl", 8, 60, message=RATE_LIMIT_WAIT)(request, response, session)

    if honeypot_tripped(body.hp_url):
        return SuccessOut()

    if not (body.business_name and body.contact_name and body.phone and body.employees):
        return JSONResponse({"detail": "Required fields missing."}, status_code=400)

    meta = request_meta(request)
    is_discovery = body.source == "discovery"

    crud.create_template_download(
        session,
        TemplateDownload(
            business_name=body.business_name,
            employees=body.employees,
            contact_name=body.contact_name,
            phone=body.phone,
            email=body.email or "",
            template_name=body.template_name or "",
            consent_contact=bool(body.consent_contact),
            report_token=body.report_token or "",  # discovery: stores the niche id
            source=body.source or "report_page",
            ip_address=meta.ip,
            city=meta.city,
            country=meta.country,
            created_at_attr=iso(now_utc()),
        ),
    )

    if body.consent_briefings and body.email:
        _upsert_subscriber_safely(
            session,
            email=body.email,
            name=body.contact_name,
            source="template_form",
            ip=meta.ip,
            city=meta.city,
            country=meta.country,
            now=now_utc(),
        )

    # Discovery: email the user their inventory + alert admin (fire-and-forget; the user
    # already has the file via client-side download).
    if is_discovery:
        nice_name = body.niche_name or "your business"
        if body.email and body.inventory_csv:
            html = email_service.render(
                "forms/discovery_inventory.html", name=body.contact_name, niche_name=nice_name
            )
            background.add_task(
                _send_safely,
                body.email,
                f"Your DPDPA personal data inventory — {nice_name}",
                html,
                from_=settings.EMAILS_FROM_NOREPLY,
                attachments=[
                    {
                        "filename": DISCOVERY_CSV_FILENAME,
                        "content": body.inventory_csv.encode("utf-8"),
                        "content_type": "text/csv",
                    }
                ],
            )
        alert_html = email_service.render(
            "forms/discovery_lead_alert.html",
            lead={
                "name": body.contact_name,
                "business_name": body.business_name,
                "email": body.email or "",
                "phone": body.phone,
                "employees": body.employees,
                "niche_name": nice_name,
                "location": ", ".join(p for p in (meta.city, meta.country) if p),
            },
        )
        background.add_task(
            _send_safely,
            admin_email(),
            f"New Data Discovery lead — {body.business_name}",
            alert_html,
            from_=settings.EMAILS_FROM_NOREPLY,
        )

    return SuccessOut()


# ── 6. POST /forms/templates/download ─────────────────────────────────────
class TemplatesDownloadIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    email: str | None = None
    contact_person_name: str | None = Field(default=None, alias="contactPersonName")
    business_name: str | None = Field(default=None, alias="businessName")
    template_selected: str | None = Field(default=None, alias="templateSelected")
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    consent_contact: bool | None = Field(default=None, alias="consentContact")
    consent_briefings: bool | None = Field(default=None, alias="consentBriefings")
    hp_url: str | None = None


class TemplatesDownloadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
    message: str | None = None
    download_url: str | None = Field(default=None, serialization_alias="downloadUrl")
    email: bool | None = None
    whatsapp: bool | None = None


SOURCE_MAX_LENGTH = 64  # `template_downloads.source` is capped at 64 chars


def valid_indian_phone_number(phone: str) -> bool:
    try:
        return phonenumbers.is_valid_number(phonenumbers.parse(phone, "IN"))
    except Exception:
        return False


def format_phone_number(phone: str) -> str:
    try:
        parsed = phonenumbers.parse(phone, "IN")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164) or phone
    except Exception:
        return phone


def validate_template_download(body: TemplatesDownloadIn) -> dict[str, list[str]]:
    """TemplateDownloadFormSchema (zod) — same fields, same messages, same order."""
    errors: dict[str, list[str]] = {}

    try:
        validate_email(body.email or "", check_deliverability=False)
    except EmailNotValidError:
        errors["email"] = ["Please enter a valid email address"]

    name = body.contact_person_name or ""
    if len(name) < 2:
        errors["contactPersonName"] = ["Name must be at least 2 characters"]
    elif len(name) > 100:
        errors["contactPersonName"] = ["Name too long"]

    business = body.business_name or ""
    if len(business) < 2:
        errors["businessName"] = ["Business name must be at least 2 characters"]
    elif len(business) > 200:
        errors["businessName"] = ["Business name too long"]

    if body.template_selected not in TEMPLATE_OPTIONS:
        errors["templateSelected"] = ["Please select a template"]

    if not valid_indian_phone_number(body.phone_number or ""):
        errors["phoneNumber"] = ["Please enter a valid Indian phone number (+91 format)"]

    if body.consent_contact is None:
        errors["consentContact"] = ["Required"]
    if body.consent_briefings is None:
        errors["consentBriefings"] = ["Required"]

    return errors


def template_download_url(template_id: str) -> str:
    """Vercel Blob is gone: templates ship in frontend/public/templates."""
    site = (settings.NEXT_PUBLIC_SITE_URL or "https://saralprivacy.com").rstrip("/")
    return f"{site}/templates/{template_id}{TEMPLATE_EXTENSIONS[template_id]}"


def send_template_whatsapp(
    *, phone_number: str, contact_person_name: str, template_name: str, download_url: str
) -> bool:
    """Optional — degrades gracefully when the Twilio env vars are missing.

    Raw Twilio REST call: the SDK was 9.6 MB for this one request; basic auth plus a
    form body is the whole API.
    """
    sid = settings.TWILIO_ACCOUNT_SID
    token = settings.TWILIO_AUTH_TOKEN
    sender = settings.TWILIO_WHATSAPP_FROM
    if not (sid and token and sender):
        log.info("Twilio not configured; skipping WhatsApp")
        return False

    body = (
        f"Hi {contact_person_name}! 👋\n\n"
        f"Your *{template_name}* from SaralPrivacy is ready.\n\n"
        f"⬇ Download here:\n{download_url}\n\n"
        "— SaralPrivacy DPDPA Templates"
    )
    try:
        res = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": f"whatsapp:{sender}", "To": f"whatsapp:{phone_number}", "Body": body},
            timeout=20,
        )
        payload = res.json()
        if res.status_code >= 400 or not payload.get("sid"):
            log.error("WhatsApp send failed: %s", payload.get("message") or res.status_code)
            return False
        return True
    except Exception:
        log.exception("WhatsApp send failed")
        return False


@router.post(
    "/templates/download", response_model=TemplatesDownloadOut, response_model_exclude_none=True
)
def templates_download(
    body: TemplatesDownloadIn, request: Request, response: Response, session: SessionDep
) -> Any:
    # Same guards as every other public POST — this one can burn Twilio spend.
    if honeypot_tripped(body.hp_url):
        return TemplatesDownloadOut()  # silent drop for bots

    RateLimit("template-download", 5, 10 * 60, message=RATE_LIMIT_LATER)(request, response, session)

    errors = validate_template_download(body)
    if errors:
        return JSONResponse({"message": "Invalid form data", "errors": errors}, status_code=400)

    template_id = str(body.template_selected)  # narrowed by validate_template_download
    template_name = TEMPLATE_NAMES[template_id]
    formatted_phone = format_phone_number(body.phone_number or "")
    download_url = template_download_url(template_id)

    meta = request_meta(request)

    # Save the lead (non-fatal if it fails). Key set pinned to the table, as lib/templates/lead.ts was.
    try:
        crud.create_template_download(
            session,
            TemplateDownload(
                email=body.email,
                contact_name=body.contact_person_name or "",
                business_name=body.business_name or "",
                template_name=template_name,
                phone=formatted_phone,
                consent_contact=bool(body.consent_contact),
                source=(request.headers.get("referer") or "direct")[:SOURCE_MAX_LENGTH],
                ip_address=meta.ip,
                city=meta.city,
                country=meta.country,
                created_at_attr=iso(now_utc()),
            ),
        )
    except Exception:
        session.rollback()
        log.exception("[templates/download] Lead save failed (non-fatal)")

    if body.consent_briefings:
        _upsert_subscriber_safely(
            session,
            email=body.email or "",
            name=body.contact_person_name or "",
            source="template_download",
            ip=meta.ip,
            city=meta.city,
            country=meta.country,
            now=now_utc(),
        )

    # Send email (required — fail the request if this fails).
    try:
        html = email_service.render(
            "forms/template_ready.html",
            contact_person_name=body.contact_person_name or "",
            business_name=body.business_name or "",
            template_name=template_name,
            download_url=download_url,
            consent_briefings=bool(body.consent_briefings),
        )
        email_service.send(
            body.email or "",
            f'Your "{template_name}" is ready — download now',
            html,
            from_=settings.EMAILS_FROM_NOREPLY,
        )
    except Exception:
        log.exception("[templates/download] Email send failed")
        return JSONResponse(
            {"message": "Failed to send template email. Please try again."}, status_code=500
        )

    whatsapp_sent = False
    if body.consent_contact:
        whatsapp_sent = send_template_whatsapp(
            phone_number=formatted_phone,
            contact_person_name=body.contact_person_name or "",
            template_name=template_name,
            download_url=download_url,
        )

    return TemplatesDownloadOut(
        message="Template sent successfully",
        download_url=download_url,
        email=True,
        whatsapp=whatsapp_sent,
    )


# ── 7. POST /forms/white-paper ────────────────────────────────────────────
class WhitePaperIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    full_name: str | None = Field(default=None, alias="fullName")
    work_email: str | None = Field(default=None, alias="workEmail")
    company_name: str | None = Field(default=None, alias="companyName")
    industry: str | None = None
    company_size: str | None = Field(default=None, alias="companySize")
    phone: str | None = None
    language: str | None = None
    consent_email: bool | None = Field(default=None, alias="consentEmail")
    consent_phone: bool | None = Field(default=None, alias="consentPhone")
    consent_webinars: bool | None = Field(default=None, alias="consentWebinars")
    hp_url: str | None = None


class WhitePaperOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
    download_url: str | None = Field(default=None, serialization_alias="downloadUrl")
    language: str | None = None
    partial: bool | None = None
    message: str | None = None


def resolve_language(code: str | None) -> str:
    """getLanguage(): anything unknown falls back to English."""
    return code if code in GUIDE_PDF_URLS else DEFAULT_LANG_CODE


def guide_pdf_url(code: str) -> str:
    """getPdfUrl(): falls back to the English file for any language not yet published."""
    return GUIDE_PDF_URLS.get(code) or str(GUIDE_PDF_URLS[DEFAULT_LANG_CODE])


@router.post("/white-paper", response_model=WhitePaperOut, response_model_exclude_none=True)
def white_paper(
    body: WhitePaperIn,
    request: Request,
    response: Response,
    session: SessionDep,
    background: BackgroundTasks,
) -> Any:
    # Guard parity with the other public lead routes (this one writes PII and sends mail).
    if honeypot_tripped(body.hp_url):
        return WhitePaperOut()  # silent drop for bots

    RateLimit("white-paper", 5, 10 * 60, message=RATE_LIMIT_LATER)(request, response, session)

    language = resolve_language(body.language)

    if not (
        body.full_name
        and body.work_email
        and body.company_name
        and body.industry
        and body.company_size
    ):
        return JSONResponse({"detail": "Required fields are missing."}, status_code=400)

    meta = request_meta(request)
    now = now_utc()

    download = Download(
        name=body.full_name,
        email=body.work_email,
        phone=body.phone or "",
        company=body.company_name,
        industry=body.industry,
        company_size=body.company_size,
        language=language,
        consent_email=bool(body.consent_email),
        consent_phone=bool(body.consent_phone),
        consent_webinars=bool(body.consent_webinars),
        privacy_version=PRIVACY_NOTICE_VERSION,
        downloaded_at=now,
        ip_address=meta.ip,
        city=meta.city,
        country=meta.country,
        region=meta.region,
    )
    crud.create_download(session, download)

    # One consent-log row per consent type checked.
    timestamp = iso(now)
    for flag, consent_type in (
        (body.consent_email, "email_marketing"),
        (body.consent_phone, "phone_contact"),
        (body.consent_webinars, "webinars"),
    ):
        if flag:
            _log_consent_safely(
                session,
                email=body.work_email,
                name=body.full_name,
                source="download",
                consent_type=consent_type,
                timestamp=timestamp,
                ip_address=meta.ip,
                user_agent=meta.user_agent,
                city=meta.city,
                country=meta.country,
                region=meta.region,
            )

    alert_html = email_service.render("forms/download_alert.html", download=download)
    background.add_task(
        _send_safely,
        admin_email(),
        f"White Paper Downloaded — {download.name} from {download.company}",
        alert_html,
        from_=settings.EMAILS_FROM_NOREPLY,
    )

    if body.consent_email:
        _upsert_subscriber_safely(
            session,
            email=body.work_email,
            name=body.full_name,
            industry=body.industry,
            source="whitepaper_form",
            ip=meta.ip,
            user_agent=meta.user_agent,
            city=meta.city,
            country=meta.country,
            region=meta.region,
            now=now,
        )

    return WhitePaperOut(
        download_url=guide_pdf_url(language),
        language=language,
        partial=GUIDE_PDF_URLS.get(language) is None and language != DEFAULT_LANG_CODE,
        message="Download ready.",
    )


__all__ = ["router"]
