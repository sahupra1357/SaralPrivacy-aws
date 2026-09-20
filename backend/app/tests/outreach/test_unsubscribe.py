from fastapi.testclient import TestClient
from sqlmodel import Session

from app.tests.outreach.helpers import make_contact

PATH = "/api/v1/outreach/unsubscribe"


def test_unsubscribe_marks_the_contact(client: TestClient, session: Session) -> None:
    contact = make_contact(session, token="tok-u")

    res = client.post(PATH, json={"token": "tok-u"})

    assert res.status_code == 200
    assert res.json() == {"success": True, "already": False}
    session.refresh(contact)
    assert contact.status == "unsubscribed"


def test_unsubscribe_reports_already_removed(client: TestClient, session: Session) -> None:
    make_contact(session, status="unsubscribed", token="tok-u2")

    res = client.post(PATH, json={"token": "tok-u2"})

    assert res.json() == {"success": True, "already": True}


def test_unsubscribe_rejects_a_missing_token(client: TestClient) -> None:
    res = client.post(PATH, json={})

    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid token."


def test_unsubscribe_rejects_an_unknown_token(client: TestClient) -> None:
    res = client.post(PATH, json={"token": "nope"})

    assert res.status_code == 404
    assert res.json()["detail"] == "Link not recognised."
