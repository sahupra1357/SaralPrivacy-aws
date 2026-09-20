"""POST /admin/send-report — the survey result email for one assessment."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.api.routes import admin as routes
from app.core.config import settings

URL = "/api/v1/admin/send-report"


@pytest.fixture
def assessment(insert_row: Callable[..., dict[str, Any]]) -> dict[str, Any]:
    return insert_row(
        "app.assessments",
        email="owner@clinic.test",
        industry="healthcare",
        risk_level="amber",
        name="Meera",
        business_name="Sunrise Clinic",
        final_score=42,
        verdict_band="Building Foundations",
        report_token="tok123",
        immediate_actions_json=json.dumps(["Appoint a privacy owner"]),
        red_flags_json=json.dumps(["No breach plan"]),
        answers_json=json.dumps({"q1": "a"}),
        category_scores_json=json.dumps({"noticeConsent": 55, "accessControl": 20}),
    )


def test_sends_the_report_and_records_the_send(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    assessment: dict[str, Any],
    mock_email,
) -> None:
    res = client.post(
        URL,
        json={
            "assessmentId": str(assessment["id"]),
            "answerSummary": [{"question": "Who owns privacy?", "answer": "Nobody yet"}],
        },
        headers=admin_headers,
    )

    assert res.status_code == 200
    assert res.json() == {"success": True}
    sent = mock_email.last()
    assert sent["to"] == ["owner@clinic.test"]
    assert sent["from_"] == settings.EMAILS_FROM_BRIEFINGS
    assert sent["subject"] == "Your DPDPA Score: 42/100 — You're building. Here's the path to 70+"
    assert "Sunrise Clinic" in sent["html"]
    assert "Appoint a privacy owner" in sent["html"]
    assert "No breach plan" in sent["html"]
    assert "Nobody yet" in sent["html"]
    assert "https://saralprivacy.com/report/tok123" in sent["html"]
    assert routes.BAND_DESCRIPTIONS["Building Foundations"] in sent["html"]

    row = session.execute(
        text("select email_sent_at, email_sent_by from app.assessments where id = :id"),
        {"id": assessment["id"]},
    ).one()
    assert row.email_sent_by == "admin"
    assert row.email_sent_at and row.email_sent_at.endswith("Z")


def test_resolves_a_legacy_appwrite_id(
    client: TestClient,
    admin_headers: dict[str, str],
    insert_row: Callable[..., dict[str, Any]],
    mock_email,
) -> None:
    insert_row(
        "app.assessments", email="l@x.test", industry="it", risk_level="red", legacy_id="65abcdef"
    )
    res = client.post(URL, json={"assessmentId": "65abcdef"}, headers=admin_headers)
    assert res.status_code == 200
    assert mock_email.last()["to"] == ["l@x.test"]


def test_bad_stored_json_falls_back_to_empty(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    row = insert_row(
        "app.assessments",
        email="j@x.test",
        industry="it",
        risk_level="red",
        immediate_actions_json="{not json",
        category_scores_json="[]",
    )
    res = client.post(URL, json={"assessmentId": str(row["id"])}, headers=admin_headers)
    assert res.status_code == 200


def test_build_report_data_defaults() -> None:
    data = routes.build_report_data(
        {"email": "a@b.c", "overall_score": 12.0, "verdict_band": None}, []
    )
    assert data["score"] == 12.0
    assert data["band"] == "Early Stage"
    assert data["summary"] == ""
    assert data["category_scores"] == {
        "notice_consent": 0,
        "access_control": 0,
        "retention_deletion": 0,
        "ownership_governance": 0,
        "vendor_partner_risk": 0,
        "incident_readiness": 0,
    }


def test_requires_an_assessment_id(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(URL, json={}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json()["detail"] == "assessmentId is required"


def test_unknown_assessment_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(
        URL, json={"assessmentId": "0190a1b2-0000-7000-8000-000000000000"}, headers=admin_headers
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Assessment not found"


def test_assessment_without_email_is_400(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    row = insert_row("app.assessments", email="", industry="it", risk_level="red")
    res = client.post(URL, json={"assessmentId": str(row["id"])}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json()["detail"] == "Assessment has no email address"


def test_email_failure_is_500_and_nothing_is_recorded(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    assessment: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import email

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("Resend rejected the message")

    monkeypatch.setattr(email, "send", boom)
    res = client.post(URL, json={"assessmentId": str(assessment["id"])}, headers=admin_headers)

    assert res.status_code == 500
    assert res.json()["detail"] == "Resend rejected the message"
    sent_at = session.execute(
        text("select email_sent_at from app.assessments where id = :id"), {"id": assessment["id"]}
    ).scalar()
    assert sent_at is None


def test_is_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.post(URL, json={"assessmentId": "x"}, headers=blogger_headers).status_code == 403
    assert client.post(URL, json={"assessmentId": "x"}).status_code == 401
