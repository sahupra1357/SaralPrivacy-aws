from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.forms import Subscriber, TemplateDownload
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/template-download"

PAYLOAD = {
    "businessName": "Sunrise Salon",
    "employees": "1-10",
    "contactName": "Meera",
    "phone": "+919812345678",
    "email": "meera@example.com",
    "consentContact": True,
    "consentBriefings": False,
    "templateName": "Privacy Notice Template",
}


def test_template_download_stores_the_lead(client: TestClient, session: Session) -> None:
    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 200
    assert res.json() == {"success": True}

    row = session.exec(select(TemplateDownload)).one()
    assert row.business_name == "Sunrise Salon"
    assert row.contact_name == "Meera"
    assert row.source == "report_page"
    assert row.report_token == ""
    assert row.consent_contact is True


def test_template_download_subscribes_when_briefings_consent_is_given(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json={**PAYLOAD, "consentBriefings": True})

    sub = session.exec(select(Subscriber)).one()
    assert sub.email == "meera@example.com"
    assert sub.status == "active"
    assert sub.consent_source == "manual"


def test_template_download_does_not_subscribe_without_consent(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json=PAYLOAD)

    assert session.exec(select(Subscriber)).first() is None


def test_discovery_emails_the_inventory_and_alerts_admin(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(
        PATH,
        json={
            **PAYLOAD,
            "source": "discovery",
            "reportToken": "salons",
            "nicheName": "Salons & Spas",
            "inventoryCsv": "field,purpose\nname,booking\n",
        },
    )

    assert res.status_code == 200

    row = session.exec(select(TemplateDownload)).one()
    assert row.source == "discovery"
    assert row.report_token == "salons"

    subjects = [c["subject"] for c in mock_email.calls]
    assert "Your DPDPA personal data inventory — Salons & Spas" in subjects
    assert "New Data Discovery lead — Sunrise Salon" in subjects

    inventory = next(c for c in mock_email.calls if c["subject"].startswith("Your DPDPA"))
    assert inventory["attachments"][0]["filename"] == "dpdpa-personal-data-inventory.csv"
    assert inventory["attachments"][0]["content"] == b"field,purpose\nname,booking\n"
    assert "salons &amp; spas" in inventory["html"]


def test_discovery_alerts_admin_even_without_a_csv(
    client: TestClient, mock_email: Recorder
) -> None:
    client.post(PATH, json={**PAYLOAD, "source": "discovery", "nicheName": "Gyms"})

    subjects = [c["subject"] for c in mock_email.calls]
    assert subjects == ["New Data Discovery lead — Sunrise Salon"]


def test_template_download_requires_the_four_lead_fields(
    client: TestClient, session: Session
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "phone": ""})

    assert res.status_code == 400
    assert res.json()["detail"] == "Required fields missing."
    assert session.exec(select(TemplateDownload)).first() is None


def test_template_download_honeypot_pretends_success_and_stores_nothing(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "hp_url": "bot"})

    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert session.exec(select(TemplateDownload)).first() is None
    assert mock_email.calls == []


def test_template_download_rate_limits_after_eight_requests(client: TestClient) -> None:
    for _ in range(8):
        assert client.post(PATH, json=PAYLOAD).status_code == 200

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please wait a moment and try again."
