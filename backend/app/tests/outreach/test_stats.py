from fastapi.testclient import TestClient
from sqlmodel import Session

from app.tests.outreach.helpers import make_contact


def test_stats_counts_contacts_per_status(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    make_contact(session, "a@example.com", status="pending")
    make_contact(session, "b@example.com", status="pending")
    make_contact(session, "c@example.com", status="sent")
    make_contact(session, "d@example.com", status="failed")

    res = client.get("/api/v1/outreach/stats", headers=admin_headers)

    assert res.status_code == 200
    assert res.json() == {
        "total": 4,
        "pending": 2,
        "sent": 1,
        "subscribed": 0,
        "bounced": 0,
        "unsubscribed": 0,
        "complained": 0,
    }


def test_stats_requires_admin(client: TestClient) -> None:
    assert client.get("/api/v1/outreach/stats").status_code == 401


def test_contacts_lists_documents_filtered_by_status(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    sent = make_contact(session, "sent@example.com", status="sent")
    make_contact(session, "pending@example.com", status="pending")

    res = client.get("/api/v1/outreach/contacts?status=sent&limit=200", headers=admin_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert [d["email"] for d in body["documents"]] == ["sent@example.com"]
    assert body["documents"][0]["$id"] == str(sent.id)
    assert body["documents"][0]["status"] == "sent"


def test_contacts_without_filter_returns_all(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    make_contact(session, "one@example.com")
    make_contact(session, "two@example.com", status="subscribed")

    res = client.get("/api/v1/outreach/contacts", headers=admin_headers)

    assert {d["email"] for d in res.json()["documents"]} == {"one@example.com", "two@example.com"}


def test_contacts_requires_admin(client: TestClient) -> None:
    assert client.get("/api/v1/outreach/contacts").status_code == 401
