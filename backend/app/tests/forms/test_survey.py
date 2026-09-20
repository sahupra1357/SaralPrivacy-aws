import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import forms as routes
from app.models.forms import SurveyResponse
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/survey/submit"

ANSWERS = {
    "role": "Founder",
    "employee_size": "11-50",
    "sector": "Healthcare",
    "operating_footprint": "India only",
    "state_ut": "Karnataka",
    "city": "Bengaluru",
    "digital_personal_data": "Yes",
    "data_types": ["Name", "Phone"],
    "data_storage": ["Cloud"],
    "controls_in_place": ["Privacy notice"],
    "readiness_self_view": "Getting started",
    "biggest_blocker": "Time",
    "most_helpful_resource": "Templates",
    "name": "Priya",
    "business_name": "Priya Clinic",
    "work_email": "priya@example.com",
    "mobile_number": "+919812345678",
    "contact_preference": "Email",
    "consent_followup": True,
}
SCORE = {
    "score": 42,
    "band": "Early Stage",
    "summary": "You have made a start.",
    "recommendations": ["Publish a privacy notice", "Name a grievance officer"],
    "riskFlags": ["No retention policy"],
}


def test_survey_stores_the_response(client: TestClient, session: Session) -> None:
    res = client.post(PATH, json={"answers": ANSWERS, "score": SCORE})

    assert res.status_code == 200
    assert res.json() == {"success": True}

    row = session.exec(select(SurveyResponse)).one()
    assert row.sector == "Healthcare"
    assert row.score == 42
    assert row.score_band == "Early Stage"
    assert row.consent_given is True
    assert row.consent_version == "v1.0"
    assert row.wants_report is False
    assert json.loads(row.data_types or "[]") == ["Name", "Phone"]
    assert json.loads(row.controls_in_place or "[]") == ["Privacy notice"]


def test_survey_emails_the_report_when_opted_in(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    answers = {**ANSWERS, "want_detailed_report": "Yes, send it to me"}

    res = client.post(PATH, json={"answers": answers, "score": SCORE})

    assert res.status_code == 200
    row = session.exec(select(SurveyResponse)).one()
    assert row.wants_report is True

    sent = mock_email.last()
    assert sent["to"] == ["priya@example.com"]
    assert sent["subject"] == ("Your DPDPA Score: 42/100 — Here's exactly why and what to do first")
    assert "You have made a start." in sent["html"]
    assert "No retention policy" in sent["html"]
    assert "Publish a privacy notice" in sent["html"]


def test_survey_sends_no_email_when_not_opted_in(client: TestClient, mock_email: Recorder) -> None:
    client.post(PATH, json={"answers": ANSWERS, "score": SCORE})

    assert mock_email.calls == []


def test_survey_requires_answers_and_score(client: TestClient, session: Session) -> None:
    res = client.post(PATH, json={"answers": ANSWERS})

    assert res.status_code == 400
    assert res.json()["detail"] == "Missing answers or score"
    assert session.exec(select(SurveyResponse)).first() is None


def test_survey_honeypot_pretends_success_and_stores_nothing(
    client: TestClient, session: Session
) -> None:
    res = client.post(PATH, json={"answers": ANSWERS, "score": SCORE, "hp_url": "bot"})

    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert session.exec(select(SurveyResponse)).first() is None


def test_survey_rate_limits_after_eight_requests(client: TestClient) -> None:
    for _ in range(8):
        assert client.post(PATH, json={"answers": ANSWERS, "score": SCORE}).status_code == 200

    res = client.post(PATH, json={"answers": ANSWERS, "score": SCORE})

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please wait a moment and try again."


def test_report_subject_and_colour_vary_by_band() -> None:
    base = {"name": "Ann", "business_name": "", "summary": "s", "recommendations": []}

    strong_subject, strong_html = routes.render_survey_report(
        {**base, "score": 88, "band": "Operationally Strong"}
    )
    middle_subject, _ = routes.render_survey_report(
        {**base, "score": 61, "band": "Building Foundations"}
    )

    assert strong_subject == "Your DPDPA Score: 88/100 — Strong start. Here's what to protect"
    assert middle_subject == "Your DPDPA Score: 61/100 — You're building. Here's the path to 70+"
    assert "#16A34A" in strong_html
    assert "Book a review session to certify your controls" in strong_html
    assert "Hi Ann" in strong_html


def test_report_renders_the_scorecards_when_category_scores_are_supplied() -> None:
    _, html = routes.render_survey_report(
        {
            "name": "Ann",
            "score": 50,
            "band": "Building Foundations",
            "summary": "s",
            "recommendations": [],
            "category_scores": {
                "notice_consent": 80,
                "access_control": 40,
                "retention_deletion": 20,
                "ownership_governance": 60,
                "vendor_partner_risk": 30,
                "incident_readiness": 10,
            },
        }
    )

    assert "Your readiness by area" in html
    assert "Notice &amp; Consent" in html
    assert "Data Inventory &amp; Storage" in html
    assert "These 5 areas explain why your overall score is 50/100." in html
