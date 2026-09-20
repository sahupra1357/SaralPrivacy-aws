"""POST /api/v1/chat/handoff — the consented callback request.

`ops.leads` and `ops.consent_log` are created by the forms module's migration; these tests
read them back with plain SQL so they do not depend on that module's Python names.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.crud import chat as crud
from app.tests.conftest import Recorder

URL = "/api/v1/chat/handoff"
TOO_MANY = "Too many requests. Please wait a moment and try again."


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sessionId": "sess-1",
        "name": "Asha Rao",
        "email": "asha@example.in",
        "consent": True,
        "hp_url": "",
        "pageUrl": "/learn/consent",
        "reason": "explicit_ask",
        "lastUserMessage": "Do I need consent for WhatsApp marketing?",
        "state": {
            "journey": "J4",
            "industry": "d2c-brands",
            "messageCount": 3,
            "pagesShown": ["/learn/consent", "javascript:alert(1)"],
        },
    }
    payload.update(overrides)
    return payload


def leads(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(text("select * from ops.leads where source = 'setu_handoff'"))
    return [dict(r) for r in rows.mappings().all()]


def consents(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(text("select * from ops.consent_log where source = 'setu_handoff'"))
    return [dict(r) for r in rows.mappings().all()]


# ── Validation ────────────────────────────────────────────────────────────────
def test_handoff_rejects_a_malformed_body(client: TestClient) -> None:
    res = client.post(URL, content=b"nope", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json() == {"error": "Invalid JSON body."}


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"consent": False}, "Consent is required."),
        ({"consent": "true"}, "Consent is required."),
        ({"name": "  "}, "Please add your name."),
        ({"email": "asha-at-example"}, "Please check the email address."),
        ({"sessionId": ""}, "sessionId is required."),
    ],
)
def test_handoff_validation_errors(
    client: TestClient, session: Session, overrides: dict[str, Any], error: str
) -> None:
    res = client.post(URL, json=body(**overrides))
    assert res.status_code == 400
    assert res.json() == {"error": error}
    assert leads(session) == []


def test_honeypot_pretends_success_and_stores_nothing(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(URL, json=body(hp_url="https://spam.example"))
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert leads(session) == []
    assert mock_email.calls == []


# ── Rate limits ───────────────────────────────────────────────────────────────
def test_typos_do_not_consume_the_submission_budget(client: TestClient) -> None:
    # Three rejected submissions, then a valid one: must still go through.
    for _ in range(3):
        assert client.post(URL, json=body(email="bad")).status_code == 400
    assert client.post(URL, json=body()).status_code == 200


def test_submission_ceiling_is_three_per_ten_minutes(client: TestClient) -> None:
    for _ in range(3):
        assert client.post(URL, json=body()).status_code == 200
    res = client.post(URL, json=body())
    assert res.status_code == 429
    assert res.json() == {"error": TOO_MANY}
    assert int(res.headers["retry-after"]) >= 1


def test_raw_request_ceiling_is_fifteen_per_ten_minutes(client: TestClient) -> None:
    for _ in range(15):
        client.post(URL, json=body(email="bad"))
    res = client.post(URL, json=body())
    assert res.status_code == 429
    assert res.json() == {"error": TOO_MANY}


# ── Success ───────────────────────────────────────────────────────────────────
def test_handoff_writes_the_lead_the_consent_row_and_alerts_the_admin(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(
        URL,
        json=body(),
        headers={
            "x-vercel-ip-city": "New%20Delhi",
            "x-vercel-ip-country": "IN",
            "x-vercel-ip-country-region": "DL",
            "user-agent": "pytest",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    [lead] = leads(session)
    assert lead["name"] == "Asha Rao"
    assert lead["email"] == "asha@example.in"
    assert lead["company"] == ""
    assert lead["industry"] == "d2c-brands"
    assert lead["preferred_contact"] == "email"
    assert lead["consent_version"] == "1.0.0"
    assert lead["city"] == "New Delhi"
    assert lead["country"] == "IN"
    assert lead["created_at_attr"] is not None
    assert "Working through: Do I need consent for this?" in lead["issue_summary"]
    assert "Pages shown: /learn/consent\n" in lead["issue_summary"]
    assert "javascript:" not in lead["issue_summary"]
    assert lead["issue_summary"].endswith("Trigger: explicit_ask")

    [consent] = consents(session)
    assert consent["consent_type"] == "data_processing"
    assert consent["consent_value"] is True
    assert consent["privacy_version"] == "1.0.0"
    assert consent["user_agent"] == "pytest"
    assert consent["timestamp"].endswith("Z")

    sent = mock_email.last()
    assert sent["subject"] == "New Consultation Request — Asha Rao from "
    assert "Reply directly to this lead within 1 business day." in sent["html"]


def test_an_unknown_reason_falls_back_to_explicit_ask(client: TestClient, session: Session) -> None:
    client.post(URL, json=body(reason="because"))
    assert leads(session)[0]["issue_summary"].endswith("Trigger: explicit_ask")


def test_pii_in_the_last_message_is_redacted_before_storage(
    client: TestClient, session: Session
) -> None:
    client.post(URL, json=body(lastUserMessage="call me on 9876543210"))
    summary = leads(session)[0]["issue_summary"]
    assert "9876543210" not in summary
    assert "[phone]" in summary


# ── Failure posture ───────────────────────────────────────────────────────────
def test_a_lost_lead_is_a_visible_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mock_email: Recorder
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OperationalError("insert", {}, Exception("db down"))

    monkeypatch.setattr(crud, "insert_lead", boom)
    res = client.post(URL, json=body())
    assert res.status_code == 500
    assert res.json() == {"error": "We couldn't send that just now. Please use the contact page."}
    assert mock_email.calls == []


def test_a_lost_consent_row_does_not_cost_the_user_their_callback(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch, mock_email: Recorder
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OperationalError("insert", {}, Exception("consent_log gone"))

    monkeypatch.setattr(crud, "insert_consent_log", boom)
    res = client.post(URL, json=body())
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert len(leads(session)) == 1
    assert mock_email.calls
