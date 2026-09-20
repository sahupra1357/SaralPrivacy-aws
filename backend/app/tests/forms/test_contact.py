from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.forms import ConsentLog, Lead
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/contact"

PAYLOAD = {
    "fullName": "Asha Rao",
    "workEmail": "asha@example.com",
    "mobileNumber": "+919876543210",
    "companyName": "Rao Textiles",
    "industry": "Manufacturing",
    "companySize": "51-200",
    "issueSummary": "We need a privacy notice for our website.",
    "preferredContact": "Email",
    "preferredTime": "Mornings",
    "consentContact": True,
}


def test_contact_stores_lead_consent_and_alerts_admin(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 200
    assert res.json() == {
        "success": True,
        "message": "Your consultation request has been received. We will respond within one business day.",
    }

    lead = session.exec(select(Lead).where(Lead.email == "asha@example.com")).one()
    assert lead.source == "consultation"
    assert lead.company == "Rao Textiles"
    assert lead.consent_version == "1.0.0"
    assert lead.risk_level == ""
    assert lead.created_at_attr is not None

    consent = session.exec(select(ConsentLog).where(ConsentLog.email == "asha@example.com")).one()
    assert consent.source == "contact"
    assert consent.consent_type == "data_processing"
    assert consent.consent_value is True
    assert consent.privacy_version == "1.0.0"

    assert mock_email.last()["subject"] == "New Consultation Request — Asha Rao from Rao Textiles"


def test_contact_rejects_missing_required_fields_with_400(
    client: TestClient, session: Session
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "issueSummary": ""})

    assert res.status_code == 400
    assert res.json()["detail"] == "Required fields are missing."
    assert session.exec(select(Lead)).first() is None


def test_contact_rejects_unchecked_consent_with_400(client: TestClient) -> None:
    res = client.post(PATH, json={**PAYLOAD, "consentContact": False})

    assert res.status_code == 400
    assert res.json()["detail"] == "Required fields are missing."


def test_contact_honeypot_pretends_success_and_stores_nothing(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "hp_url": "http://spam.example"})

    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "Your request has been received."}
    assert session.exec(select(Lead)).first() is None
    assert mock_email.calls == []


def test_contact_rate_limits_after_six_requests(client: TestClient) -> None:
    for _ in range(6):
        assert client.post(PATH, json=PAYLOAD).status_code == 200

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please wait a moment and try again."
    assert int(res.headers["Retry-After"]) >= 1
