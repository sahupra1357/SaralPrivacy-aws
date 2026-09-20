from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.forms import ConsentLog, Subscriber
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/subscribe"

PAYLOAD = {
    "name": "Vikram Nair",
    "email": "vikram@example.com",
    "industry": "Retail",
    "frequency": "daily",
    "consentEmail": True,
}


def test_subscribe_creates_subscriber_consent_row_and_welcome_email(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 200
    assert res.json() == {
        "success": True,
        "message": "Subscription successful. Check your inbox for confirmation.",
    }

    sub = session.exec(select(Subscriber).where(Subscriber.email == "vikram@example.com")).one()
    assert sub.status == "active"
    assert sub.frequency == "daily"
    assert sub.consent_source == "manual"
    assert sub.consent_version == "1.0.0"
    assert sub.name == "Vikram Nair"

    consent = session.exec(select(ConsentLog)).one()
    assert consent.source == "subscribe"
    assert consent.consent_type == "email_marketing"

    sent = mock_email.last()
    assert sent["to"] == ["vikram@example.com"]
    assert sent["subject"] == "Welcome to SaralPrivacy Daily Briefings"


def test_subscribe_falls_back_to_email_local_part_when_name_blank(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json={**PAYLOAD, "name": "   "})

    sub = session.exec(select(Subscriber)).one()
    assert sub.name == "vikram"


def test_subscribe_reactivates_an_unsubscribed_row_instead_of_adding_a_second(
    client: TestClient, session: Session
) -> None:
    session.add(
        Subscriber(
            name="Vikram Nair",
            email="vikram@example.com",
            status="unsubscribed",
            consent_source="manual",
            consent_version="0.9.0",
        )
    )
    session.commit()

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 200
    rows = session.exec(select(Subscriber).where(Subscriber.email == "vikram@example.com")).all()
    assert len(rows) == 1
    assert rows[0].status == "active"
    assert rows[0].consent_version == "1.0.0"


def test_subscribe_requires_email_and_consent(client: TestClient, session: Session) -> None:
    res = client.post(PATH, json={**PAYLOAD, "consentEmail": False})

    assert res.status_code == 400
    assert res.json()["detail"] == "Email and email consent are required."
    assert session.exec(select(Subscriber)).first() is None


def test_subscribe_rejects_a_malformed_email(client: TestClient) -> None:
    res = client.post(PATH, json={**PAYLOAD, "email": "vikram@example"})

    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid email address."


def test_subscribe_honeypot_pretends_success_and_stores_nothing(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "hp_url": "bot"})

    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "Subscription successful."}
    assert session.exec(select(Subscriber)).first() is None
    assert mock_email.calls == []


def test_subscribe_rate_limits_after_six_requests(client: TestClient) -> None:
    for i in range(6):
        assert (
            client.post(PATH, json={**PAYLOAD, "email": f"user{i}@example.com"}).status_code == 200
        )

    res = client.post(PATH, json={**PAYLOAD, "email": "user7@example.com"})

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please wait a moment and try again."
