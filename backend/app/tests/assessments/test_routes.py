"""POST /api/v1/assessments and GET /api/v1/assessments/report/{token}.

Inventory: docs/build/inventory/assessments.md rows A1 and A2.
"""

import json
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.crud import assessments as crud
from app.models.assessments import Assessment

URL = "/api/v1/assessments"


def _row(session: Session, email: str) -> Assessment:
    row = session.exec(select(Assessment).where(Assessment.email == email)).first()
    assert row is not None, f"no assessment stored for {email}"
    return row


# ── A1 success ───────────────────────────────────────────────────────────────
def test_submit_stores_row_and_returns_report_token(
    client: TestClient, session: Session, general_payload: dict[str, Any]
) -> None:
    res = client.post(URL, json=general_payload)

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["reportToken"]

    row = _row(session, "Priya@example.com")
    assert row.report_token == body["reportToken"]
    assert row.industry == "retail"  # answers.q1_sector wins over `industry`
    assert row.risk_level == "Building Foundations"
    assert row.final_score == 59
    assert row.raw_score == 41
    assert row.overall_score == 59
    assert row.verdict_band == "Building Foundations"
    assert row.report_type == "quick"
    assert row.business_name == "Rangoli Retail"
    assert row.q11_blocker == "budget"
    assert row.q12_resource == "none"
    assert json.loads(row.red_flags_json or "[]") == ["No consent record kept"]
    assert json.loads(row.thirty_day_actions_json or "[]") == ["Build a data inventory"]
    assert json.loads(row.answers_json or "{}")["q1_sector"] == "retail"
    assert json.loads(row.category_scores_json or "{}")["noticeConsent"] == 70
    assert row.created_at_attr is not None
    assert row.email_sent_at is None


def test_submit_defaults_industry_and_report_type_when_absent(
    client: TestClient, session: Session
) -> None:
    res = client.post(URL, json={"email": "bare@example.com"})

    assert res.status_code == 200
    row = _row(session, "bare@example.com")
    assert row.industry == "general"
    assert row.report_type == "quick"
    assert row.risk_level == ""
    assert row.final_score == 0
    assert row.overall_score == 0
    assert row.consent_report is False


def test_submit_keeps_legacy_scores_shape(client: TestClient, session: Session) -> None:
    res = client.post(
        URL,
        json={
            "email": "legacy@example.com",
            "industry": "manufacturing",
            "riskLevel": "HIGH",
            "scores": {
                "applicability": 80,
                "maturity": 20,
                "risk": 90,
                "urgency": 70,
                "overall": 45,
            },
        },
    )

    assert res.status_code == 200
    row = _row(session, "legacy@example.com")
    assert row.industry == "manufacturing"
    assert row.risk_level == "HIGH"
    assert row.applicability_score == 80
    assert row.maturity_score == 20
    assert row.risk_score == 90
    assert row.urgency_score == 70
    assert row.overall_score == 45  # scores.overall used when result.finalScore is absent


def test_submit_sets_report_token_expiry_90_days_ahead(
    client: TestClient, session: Session, general_payload: dict[str, Any]
) -> None:
    from datetime import UTC, datetime

    client.post(URL, json=general_payload)
    row = _row(session, "Priya@example.com")

    assert row.report_token_expires_at is not None
    expires = datetime.fromisoformat(row.report_token_expires_at)
    assert 89 <= (expires - datetime.now(UTC)).days <= 90


# ── A1 guards ────────────────────────────────────────────────────────────────
def test_submit_rejects_honeypot_with_200_and_stores_nothing(
    client: TestClient, session: Session, general_payload: dict[str, Any]
) -> None:
    general_payload["hp_url"] = "http://spam.example"

    res = client.post(URL, json=general_payload)

    assert res.status_code == 200
    assert res.json() == {"success": True, "reportToken": None}
    assert (
        session.exec(select(Assessment).where(Assessment.email == "Priya@example.com")).first()
        is None
    )


def test_submit_without_email_returns_400(client: TestClient) -> None:
    res = client.post(URL, json={"name": "No Email"})

    assert res.status_code == 400
    assert res.json()["detail"] == "Email is required."


def test_submit_is_rate_limited_after_8_in_a_window(client: TestClient) -> None:
    payload = {"email": "flood@example.com"}
    statuses = [client.post(URL, json=payload).status_code for _ in range(9)]

    assert statuses[:8] == [200] * 8
    assert statuses[8] == 429
    res = client.post(URL, json=payload)
    assert res.json()["detail"] == "Too many requests. Please wait a moment and try again."
    assert int(res.headers["retry-after"]) >= 1


# ── A1 side effects ──────────────────────────────────────────────────────────
def test_submit_writes_data_processing_consent_log(
    client: TestClient, session: Session, general_payload: dict[str, Any]
) -> None:
    client.post(URL, json=general_payload)

    row = session.execute(
        text(
            "select source, consent_type, consent_value, privacy_version "
            "from ops.consent_log where email = :email"
        ),
        {"email": "Priya@example.com"},
    ).one()
    assert row[0] == "assessment"
    assert row[1] == "data_processing"
    assert row[2] is True
    assert row[3] == "1.0.0"


def test_submit_sends_admin_alert(
    client: TestClient, mock_email: Any, general_payload: dict[str, Any]
) -> None:
    client.post(URL, json=general_payload)

    alert = mock_email.calls[0]
    assert alert["subject"] == "Assessment Completed — retail | Risk: Building Foundations"
    assert "ADMIN ALERT" in alert["html"]
    assert "Priya@example.com" in alert["html"]


def test_submit_creates_subscriber_only_when_newsletter_consent_given(
    client: TestClient, session: Session, general_payload: dict[str, Any]
) -> None:
    client.post(URL, json=general_payload)
    assert _subscriber_count(session, "priya@example.com") == 0

    general_payload["email"] = "opted@example.com"
    general_payload["consentNewsletter"] = True
    client.post(URL, json=general_payload)

    assert _subscriber_count(session, "opted@example.com") == 1
    types = (
        session.execute(
            text("select consent_type from ops.consent_log where email = :e order by consent_type"),
            {"e": "opted@example.com"},
        )
        .scalars()
        .all()
    )
    assert "email_marketing" in types


def _subscriber_count(session: Session, email: str) -> int:
    return int(
        session.execute(
            text("select count(*) from ops.subscribers where email = :e"), {"e": email}
        ).scalar_one()
    )


def test_submit_sends_report_email_and_marks_it_sent(
    client: TestClient, session: Session, mock_email: Any, general_payload: dict[str, Any]
) -> None:
    general_payload["consentReport"] = True

    res = client.post(URL, json=general_payload)

    report = mock_email.calls[-1]
    assert report["to"] == ["Priya@example.com"]
    assert report["subject"] == (
        "Your DPDPA Score: 59/100 — You're building. Here's the path to 70+"
    )
    assert f"/report/{res.json()['reportToken']}" in report["html"]
    assert "Hi Priya" in report["html"]
    assert "Rangoli Retail" in report["html"]
    # Only the first three answers appear in the email.
    assert "Which sector are you in?" in report["html"]
    assert "Who owns privacy?" not in report["html"]

    row = _row(session, "Priya@example.com")
    assert row.email_sent_by == "auto"
    assert row.email_sent_at is not None


def test_submit_without_report_consent_sends_only_the_admin_alert(
    client: TestClient, mock_email: Any, general_payload: dict[str, Any]
) -> None:
    client.post(URL, json=general_payload)

    assert len(mock_email.calls) == 1
    assert mock_email.calls[0]["subject"].startswith("Assessment Completed")


def test_industry_pack_report_email_skips_the_scorecard_and_adds_the_checklist(
    client: TestClient, mock_email: Any, industry_payload: dict[str, Any]
) -> None:
    client.post(URL, json=industry_payload)

    report = mock_email.calls[-1]
    assert "Your readiness by area" not in report["html"]  # pack buckets are not the 6 categories
    assert "ca-firm-dpdpa-starter-checklist.pdf" in report["html"]
    assert "CA Firm DPDPA Starter Checklist" in report["html"]


def test_a_failing_side_effect_never_fails_the_request(
    client: TestClient, session: Session, monkeypatch: Any, general_payload: dict[str, Any]
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr("app.api.routes.assessments._send_admin_alert", boom)

    res = client.post(URL, json=general_payload)

    assert res.status_code == 200
    assert _row(session, "Priya@example.com").report_token == res.json()["reportToken"]


def test_a_failing_insert_returns_the_save_error(
    client: TestClient, monkeypatch: Any, general_payload: dict[str, Any]
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(crud, "create_assessment", boom)

    res = client.post(URL, json=general_payload)

    assert res.status_code == 500
    assert res.json()["detail"] == "Failed to save assessment."


# ── A2 report read ───────────────────────────────────────────────────────────
def test_report_returns_the_stored_document(
    client: TestClient, general_payload: dict[str, Any]
) -> None:
    token = client.post(URL, json=general_payload).json()["reportToken"]

    res = client.get(f"{URL}/report/{token}")

    assert res.status_code == 200
    doc = res.json()
    assert doc["report_token"] == token
    assert doc["business_name"] == "Rangoli Retail"
    assert doc["verdict_band"] == "Building Foundations"
    assert doc["final_score"] == 59
    assert json.loads(doc["category_scores_json"])["accessControl"] == 40
    assert doc["created_at"] is not None


def test_report_returns_404_for_an_unknown_token(client: TestClient) -> None:
    res = client.get(f"{URL}/report/00000000-0000-0000-0000-000000000000")

    assert res.status_code == 404
    assert res.json()["detail"] == "Report not found."
