from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models.forms import ConsentLog, Subscriber
from app.tests.outreach.helpers import make_contact, make_subscriber

PATH = "/api/v1/outreach/subscribe"


def test_subscribe_creates_subscriber_with_one_click_consent(
    client: TestClient, session: Session
) -> None:
    contact = make_contact(session, "Asha@Example.com", token="tok-1")

    res = client.post(PATH, json={"token": "tok-1"})

    assert res.status_code == 200
    assert res.json() == {"success": True, "already": False, "name": "Asha Rao"}
    sub = session.exec(select(Subscriber).where(Subscriber.email == "asha@example.com")).one()
    assert sub.consent_source == "intro_email_one_click"
    assert sub.status == "active"
    assert sub.frequency == "daily"
    assert sub.industry == "Fintech"
    consent = session.exec(select(ConsentLog).where(ConsentLog.email == "asha@example.com")).one()
    assert consent.source == "outreach_magic_link"
    assert consent.consent_type == "email_marketing"
    session.refresh(contact)
    assert contact.status == "subscribed"
    assert contact.subscribed_at is not None


def test_subscribe_keeps_an_existing_subscriber_row(client: TestClient, session: Session) -> None:
    make_subscriber(session, "asha@example.com")
    contact = make_contact(session, "asha@example.com", token="tok-2")

    res = client.post(PATH, json={"token": "tok-2"})

    assert res.json()["already"] is False
    rows = session.exec(select(Subscriber).where(Subscriber.email == "asha@example.com")).all()
    assert len(rows) == 1
    session.refresh(contact)
    assert contact.status == "subscribed"


def test_subscribe_reports_already_subscribed(client: TestClient, session: Session) -> None:
    make_contact(session, status="subscribed", token="tok-3", name=None)

    res = client.post(PATH, json={"token": "tok-3"})

    assert res.json() == {"success": True, "already": True, "name": ""}


def test_subscribe_rejects_a_missing_token(client: TestClient) -> None:
    res = client.post(PATH, json={"token": 42})

    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid token."


def test_subscribe_rejects_an_unknown_token(client: TestClient) -> None:
    res = client.post(PATH, json={"token": "nope"})

    assert res.status_code == 404
    assert res.json()["detail"] == "Link not recognised or already used."
