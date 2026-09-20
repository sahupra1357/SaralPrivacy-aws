"""DPDPA readiness assessments.

Replaces `webapp/app/api/assessment/route.ts` and the server-side read behind
`webapp/app/(backoffice)/report/[token]/page.tsx`.

Scoring itself is **not** here: `lib/data/dpdpa-assessment.ts` and
`lib/data/industry-assessment/**` are pure TypeScript that runs in the browser, and
they stay in the frontend. What moved is persistence, the report token, the consent
log, the subscriber opt-in and the two emails.
"""

import json
import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import RateLimit, SessionDep, client_geo
from app.core.config import settings
from app.crud import assessments as crud
from app.services import email as email_service

log = logging.getLogger(__name__)

router = APIRouter(prefix="/assessments", tags=["assessments"])

HONEYPOT_FIELD = "hp_url"
REPORT_TOKEN_TTL_DAYS = 90

#: Verbatim from lib/email-templates.ts (email-safe hex).
BAND_COLOR: dict[str, str] = {
    "Not Started": "#DC2626",
    "Early Stage": "#F97316",
    "Building Foundations": "#EAB308",
    "Progressing Well": "#22C55E",
    "Operationally Strong": "#16A34A",
}
SAFFRON = "#E07B39"

#: Band-specific next-step resource, verbatim from lib/email-templates.ts.
BAND_CTA: dict[str, dict[str, str]] = {
    "Not Started": {
        "headline": "Start here — What is DPDPA? (Plain English guide)",
        "body": (
            "You are at the beginning of your DPDPA journey. This guide explains what the "
            "law requires in plain language — no legal jargon, just the essentials for your "
            "business."
        ),
        "href": "https://saralprivacy.com/learn/what-is-dpdpa",
        "label": "Read the Plain English Guide →",
    },
    "Early Stage": {
        "headline": "Download your free DPDPA Readiness Checklist",
        "body": (
            "You have some awareness but important gaps remain. This checklist shows the 5 "
            "most common mistakes Indian businesses make and simple ways to fix them this week."
        ),
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Building Foundations": {
        "headline": "Download your free DPDPA Readiness Checklist",
        "body": (
            "Good progress. Use this checklist to find remaining gaps and close them "
            "systematically. Focus on consistency across all your data channels and vendor "
            "agreements."
        ),
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Progressing Well": {
        "headline": "Tighten your vendor agreements with our DPA template",
        "body": (
            "You are ahead of most businesses. The next step is securing your vendor chain. "
            "This template gives you the exact data processing clauses to add to your contracts."
        ),
        "href": "https://saralprivacy.com/white-paper",
        "label": "Download White Paper →",
    },
    "Operationally Strong": {
        "headline": "Book a review session to certify your controls",
        "body": (
            "Strong readiness signals across all areas. A structured review with our experts "
            "will confirm your controls are audit-ready and identify any remaining edge-case gaps."
        ),
        "href": "https://saralprivacy.com/contact",
        "label": "Book Expert Review →",
    },
}

#: Industry packs score into their own bucket keys, so the email scorecard (which is
#: hard-wired to the general engine's 6 categories) is skipped for them.
INDUSTRY_REPORT_TYPES = frozenset(
    {
        "ca-firm",
        "training",
        "recruit",
        "d2c",
        "clinic",
        "school",
        "law-firm",
        "realty",
        "hotel",
        "pharmacy",
        "fintech",
        "wellness",
    }
)

_TEMPLATE_BASE = "https://saralprivacy.com/templates"

#: report_type → (checklist url, checklist title). Verbatim from the route handler.
CHECKLISTS: dict[str, tuple[str, str]] = {
    "ca-firm": (
        f"{_TEMPLATE_BASE}/ca-firm-dpdpa-starter-checklist.pdf",
        "CA Firm DPDPA Starter Checklist",
    ),
    "recruit": (
        f"{_TEMPLATE_BASE}/recruitment-agency-dpdpa-starter-checklist.pdf",
        "Recruitment Agency DPDPA Starter Checklist",
    ),
    "d2c": (
        f"{_TEMPLATE_BASE}/d2c-brand-dpdpa-starter-checklist.pdf",
        "D2C Brand DPDPA Starter Checklist",
    ),
    "training": (
        f"{_TEMPLATE_BASE}/training-institute-dpdpa-starter-checklist.pdf",
        "Training Institute DPDPA Starter Checklist",
    ),
    "clinic": (
        f"{_TEMPLATE_BASE}/clinic-diagnostic-lab-dpdpa-starter-checklist.pdf",
        "Clinic & Diagnostic Lab DPDPA Starter Checklist",
    ),
    "school": (
        f"{_TEMPLATE_BASE}/school-college-dpdpa-starter-checklist.pdf",
        "School & College DPDPA Starter Checklist",
    ),
    "law-firm": (
        f"{_TEMPLATE_BASE}/law-firm-dpdpa-starter-checklist.pdf",
        "Law Firm DPDPA Starter Checklist",
    ),
    "realty": (
        f"{_TEMPLATE_BASE}/real-estate-dpdpa-starter-checklist.pdf",
        "Real Estate DPDPA Starter Checklist",
    ),
    "hotel": (
        f"{_TEMPLATE_BASE}/hotels-travel-dpdpa-starter-checklist.pdf",
        "Hotels & Travel DPDPA Starter Checklist",
    ),
    "pharmacy": (
        f"{_TEMPLATE_BASE}/pharmacy-dpdpa-starter-checklist.pdf",
        "Pharmacy DPDPA Starter Checklist",
    ),
    "fintech": (
        f"{_TEMPLATE_BASE}/fintech-nbfc-dpdpa-starter-checklist.pdf",
        "Fintech / NBFC DPDPA Starter Checklist",
    ),
    "wellness": (
        f"{_TEMPLATE_BASE}/gyms-salons-spas-dpdpa-starter-checklist.pdf",
        "Gym / Salon / Spa DPDPA Starter Checklist",
    ),
}

#: The 5 display rows mapped from the engine's 6 categories (display layer only).
SCORECARD_ROWS = (
    ("Notice & Consent", "noticeConsent"),
    ("Data Inventory & Storage", None),  # mean of retentionDeletion + vendorPartnerRisk
    ("Data Principal Rights & Control", "accessControl"),
    ("Ownership & Governance", "ownershipGovernance"),
    ("Incident & Operational Readiness", "incidentReadiness"),
)


# ── Request / response models ────────────────────────────────────────────────
class ScoresIn(BaseModel):
    """Legacy industry-assessment scores (pre-engine submissions)."""

    applicability: float | None = None
    maturity: float | None = None
    risk: float | None = None
    urgency: float | None = None
    overall: float | None = None


class ResultIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    raw_score: float | None = Field(default=None, alias="rawScore")
    final_score: float | None = Field(default=None, alias="finalScore")
    verdict_band: str | None = Field(default=None, alias="verdictBand")
    verdict_description: str | None = Field(default=None, alias="verdictDescription")
    data_exposure: float | None = Field(default=None, alias="dataExposure")
    control_maturity: float | None = Field(default=None, alias="controlMaturity")
    operational_readiness: float | None = Field(default=None, alias="operationalReadiness")
    category_scores: dict[str, float] | None = Field(default=None, alias="categoryScores")
    red_flags_triggered: list[str] = Field(default_factory=list, alias="redFlagsTriggered")
    immediate_actions: list[str] = Field(default_factory=list, alias="immediateActions")
    thirty_day_actions: list[str] = Field(default_factory=list, alias="thirtyDayActions")


class AnswerRow(BaseModel):
    question: str
    answer: str


class AssessmentIn(BaseModel):
    """Both submission shapes the route handler accepted, plus the honeypot."""

    model_config = ConfigDict(populate_by_name=True)

    email: str | None = None

    # Legacy industry-assessment fields
    industry: str | None = None
    risk_level: str | None = Field(default=None, alias="riskLevel")
    scores: ScoresIn | None = None

    # General / pack assessment fields
    name: str | None = None
    business: str | None = None
    mobile: str | None = None
    report_type: str | None = None
    answers: dict[str, Any] | None = None
    result: ResultIn | None = None
    consent_report: bool | None = Field(default=None, alias="consentReport")
    consent_newsletter: bool | None = Field(default=None, alias="consentNewsletter")
    consent_followup: bool | None = Field(default=None, alias="consentFollowup")

    city: str | None = None

    #: Question/answer pairs in words, built in the browser from the same question data
    #: the form renders (the frontend keeps that data — see the inventory). Only the
    #: general assessment sends it; industry packs never produced a summary.
    answer_summary: list[AnswerRow] = Field(default_factory=list, alias="answerSummary")

    #: Hidden field only bots fill.
    hp_url: str | None = None


class AssessmentOut(BaseModel):
    # FastAPI serializes response models with by_alias=True, so the wire name is
    # `reportToken` exactly as the route handler returned it.
    model_config = ConfigDict(populate_by_name=True)

    success: bool = True
    report_token: str | None = Field(default=None, alias="reportToken")


class AssessmentReportOut(BaseModel):
    """The stored document as the report page consumed it (Appwrite-era field names)."""

    id: str
    email: str
    industry: str
    risk_level: str
    created_at: datetime | None = None
    report_type: str | None = None
    name: str | None = None
    business_name: str | None = None
    verdict_band: str | None = None
    final_score: int | None = None
    overall_score: float | None = None
    raw_score: int | None = None
    data_exposure: int | None = None
    control_maturity: int | None = None
    operational_readiness: int | None = None
    red_flags_json: str | None = None
    immediate_actions_json: str | None = None
    thirty_day_actions_json: str | None = None
    answers_json: str | None = None
    category_scores_json: str | None = None
    report_token: str | None = None
    report_token_expires_at: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────
def _round_half_up(value: float) -> int:
    """JS `Math.round` rounds .5 away from zero; Python's `round` is banker's rounding."""
    return math.floor(value + 0.5)


def _site_url() -> str:
    return settings.NEXT_PUBLIC_SITE_URL.rstrip("/") or "https://saralprivacy.com"


def _geo(request: Request, body: AssessmentIn) -> dict[str, str]:
    """Same precedence the route handler used. Note the IP stored on the row is the
    *leftmost* forwarded hop — deliberately not the rate-limit IP, which is the
    rightmost (see `client_ip` / `abuseGuard.getClientIp`)."""
    xff = request.headers.get("x-forwarded-for", "")
    ip = xff.split(",")[0].strip() if xff else (request.headers.get("x-real-ip") or "")
    self_reported = (body.city or "").strip()
    city = self_reported or client_geo(request).city
    return {
        "ip": ip,
        "city": city,
        "country": client_geo(request).country,
        "region": client_geo(request).region,
        "user_agent": request.headers.get("user-agent") or "",
    }


def _score_status(score: float) -> tuple[str, str]:
    if score >= 70:
        return "Strong", "#16A34A"
    if score >= 40:
        return "Developing", SAFFRON
    return "Needs Work", "#DC2626"


def _scorecard(cats: dict[str, float] | None) -> list[dict[str, Any]]:
    if not cats:
        return []
    data_inventory = _round_half_up(
        (cats.get("retentionDeletion", 0) + cats.get("vendorPartnerRisk", 0)) / 2
    )
    rows: list[dict[str, Any]] = []
    for label, key in SCORECARD_ROWS:
        raw = data_inventory if key is None else cats.get(key, 0)
        pct = min(100, max(0, raw))
        label_text, color = _score_status(pct)
        rows.append(
            {
                "label": label,
                "score": _as_number(raw),
                "filled": _round_half_up(pct * 2.36),
                "status": label_text,
                "color": color,
            }
        )
    return rows


def _as_number(value: float) -> float | int:
    """Render 70.0 as `70`, exactly as JavaScript prints an integral number."""
    return int(value) if float(value).is_integer() else value


def _alert_score(value: float | None) -> str:
    """`${assessment.x ?? '—'}` — a 0 still prints as "0"."""
    return "—" if value is None else str(_as_number(value))


def _send_admin_alert(row_values: dict[str, Any]) -> None:
    risk_level = str(row_values["risk_level"])
    industry = str(row_values["industry"])
    risk_color = (
        "#E53E3E" if risk_level == "HIGH" else SAFFRON if risk_level == "MEDIUM" else "#2D9B6F"
    )
    html = email_service.render(
        "assessment_alert.html",
        email=row_values["email"],
        industry=industry,
        risk_level=risk_level,
        risk_color=risk_color,
        applicability_score=_alert_score(row_values["applicability_score"]),
        maturity_score=_alert_score(row_values["maturity_score"]),
        risk_score=_alert_score(row_values["risk_score"]),
        urgency_score=_alert_score(row_values["urgency_score"]),
        overall_score=_alert_score(row_values["overall_score"]),
    )
    email_service.send(
        settings.ADMIN_EMAIL or "dilip.sahu@gmail.com",
        f"Assessment Completed — {industry} | Risk: {risk_level}",
        html,
        from_=settings.EMAILS_FROM_NOREPLY,
    )


def _survey_subject(band: str, score: float | int) -> str:
    printed = _as_number(score)
    if band in ("Not Started", "Early Stage"):
        return f"Your DPDPA Score: {printed}/100 — Here's exactly why and what to do first"
    if band == "Operationally Strong":
        return f"Your DPDPA Score: {printed}/100 — Strong start. Here's what to protect"
    return f"Your DPDPA Score: {printed}/100 — You're building. Here's the path to 70+"


def _send_survey_result(
    *,
    to: str,
    name: str,
    business_name: str,
    score: float,
    band: str,
    summary: str,
    recommendations: list[str],
    risk_flags: list[str],
    answer_summary: list[AnswerRow],
    report_token: str,
    category_scores: dict[str, float] | None,
    report_type: str,
) -> None:
    band_color = BAND_COLOR.get(band, SAFFRON)
    cta = BAND_CTA.get(band, BAND_CTA["Early Stage"])
    checklist_url, checklist_title = CHECKLISTS.get(report_type, ("", ""))
    cats = None if report_type in INDUSTRY_REPORT_TYPES else category_scores

    html = email_service.render(
        "assessment_survey_result.html",
        greeting=f"Hi {name}" if name else "Hi there",
        business_name=business_name,
        score=_as_number(score),
        band_upper=band.upper(),
        band_color=band_color,
        bar_width=max(4, _round_half_up(score * 5.36)),
        summary=summary,
        risk_flags=risk_flags,
        scorecard=_scorecard(cats),
        top_answers=[row.model_dump() for row in answer_summary[:3]],
        recommendations=recommendations,
        checklist_url=checklist_url,
        checklist_title=checklist_title,
        cta_headline=cta["headline"],
        cta_body=cta["body"],
        cta_href=cta["href"],
        cta_label=cta["label"],
        report_url=f"{_site_url()}/report/{report_token}"
        if report_token
        else "https://saralprivacy.com/assessment",
    )
    email_service.send(
        to,
        _survey_subject(band, score),
        html,
        from_=settings.EMAILS_FROM_BRIEFINGS,
    )


def _build_row(
    body: AssessmentIn, geo: dict[str, str], token: str, expires_at: str
) -> dict[str, Any]:
    result = body.result or ResultIn()
    scores = body.scores or ScoresIn()
    answers = body.answers or {}
    sector = answers.get("q1_sector")
    return {
        # Legacy fields, kept for backward compatibility with industry assessments
        "email": body.email or "",
        "industry": (sector if isinstance(sector, str) and sector else None)
        or body.industry
        or "general",
        "risk_level": result.verdict_band or body.risk_level or "",
        "applicability_score": scores.applicability if scores.applicability is not None else 0,
        "maturity_score": scores.maturity if scores.maturity is not None else 0,
        "risk_score": scores.risk if scores.risk is not None else 0,
        "urgency_score": scores.urgency if scores.urgency is not None else 0,
        "overall_score": _coalesce(result.final_score, scores.overall, 0),
        # General assessment fields
        "raw_score": _int_or_zero(result.raw_score),
        "final_score": _int_or_zero(result.final_score),
        "verdict_band": result.verdict_band or "",
        "data_exposure": _int_or_zero(result.data_exposure),
        "control_maturity": _int_or_zero(result.control_maturity),
        "operational_readiness": _int_or_zero(result.operational_readiness),
        "red_flags_json": json.dumps(result.red_flags_triggered),
        "q11_blocker": _answer_text(answers.get("q11_blocker")),
        "q12_resource": _answer_text(answers.get("q12_resource")),
        "immediate_actions_json": json.dumps(result.immediate_actions),
        "thirty_day_actions_json": json.dumps(result.thirty_day_actions),
        "report_type": body.report_type or "quick",
        "name": body.name or "",
        "business_name": body.business or "",
        "mobile": body.mobile or "",
        "consent_report": bool(body.consent_report),
        "consent_newsletter": bool(body.consent_newsletter),
        "consent_followup": bool(body.consent_followup),
        # Geo / session
        "created_at_attr": datetime.now(UTC),
        "ip_address": geo["ip"],
        "city": geo["city"],
        "country": geo["country"],
        "region": geo["region"],
        # Report delivery
        "report_token": token,
        "report_token_expires_at": expires_at,
        "answers_json": json.dumps(answers),
        "category_scores_json": json.dumps(result.category_scores or {}),
    }


def _coalesce(*values: float | None) -> float:
    for value in values:
        if value is not None:
            return value
    return 0


def _int_or_zero(value: float | None) -> int:
    return int(value) if value is not None else 0


def _answer_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return ""


# ── Routes ───────────────────────────────────────────────────────────────────
@router.post(
    "",
    dependencies=[Depends(RateLimit("assessment", 8, 60))],
    summary="Save a completed DPDPA readiness assessment",
)
def submit_assessment(body: AssessmentIn, request: Request, session: SessionDep) -> AssessmentOut:
    # Honeypot: a hidden field only bots fill. Pretend success, store nothing.
    if body.hp_url and body.hp_url.strip():
        return AssessmentOut(success=True)

    if not body.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email is required.")

    geo = _geo(request, body)
    token = str(uuid.uuid4())
    expires_at = (datetime.now(UTC) + timedelta(days=REPORT_TOKEN_TTL_DAYS)).isoformat()
    values = _build_row(body, geo, token, expires_at)

    try:
        row = crud.create_assessment(session, values)
    except Exception:
        log.exception("Assessment save error")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to save assessment."
        ) from None

    # Everything below was fire-and-forget in the route handler: a failure is logged
    # and never changes the response.
    try:
        crud.log_consent(
            session,
            email=body.email,
            source="assessment",
            consent_type="data_processing",
            ip_address=geo["ip"],
            user_agent=geo["user_agent"],
            city=geo["city"],
            country=geo["country"],
            region=geo["region"],
        )
    except Exception:
        log.exception("consent_log write error")

    try:
        _send_admin_alert(values)
    except Exception:
        log.exception("sendAssessmentAlert error")

    if body.consent_newsletter:
        try:
            crud.upsert_subscriber(
                session,
                email=body.email,
                name=body.name or "",
                industry=str(values["industry"]),
                source="assessment_form",
                ip_address=geo["ip"],
                user_agent=geo["user_agent"],
                city=geo["city"],
                country=geo["country"],
                region=geo["region"],
            )
        except Exception:
            log.exception("upsertSubscriber assessment")

    if body.consent_report:
        result = body.result or ResultIn()
        try:
            _send_survey_result(
                to=body.email,
                name=body.name or "",
                business_name=body.business or "",
                score=result.final_score if result.final_score is not None else 0,
                band=result.verdict_band or "Early Stage",
                summary=result.verdict_description or "",
                recommendations=result.immediate_actions,
                risk_flags=result.red_flags_triggered,
                answer_summary=body.answer_summary,
                report_token=token,
                category_scores=result.category_scores,
                report_type=body.report_type or "quick",
            )
            crud.mark_report_email_sent(session, row.id, when=datetime.now(UTC))
        except Exception:
            log.exception("sendSurveyResultEmail error")

    return AssessmentOut(success=True, report_token=token)


@router.get(
    "/report/{token}",
    dependencies=[Depends(RateLimit("assessment_report", 60, 60))],
    summary="Read one assessment by its report token",
)
def get_report(token: str, session: SessionDep) -> AssessmentReportOut:
    row = crud.get_by_report_token(session, token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found.")
    data = row.model_dump()
    data["id"] = str(row.id)
    data["created_at"] = row.created_at_attr or row.created_at
    return AssessmentReportOut.model_validate(data)
