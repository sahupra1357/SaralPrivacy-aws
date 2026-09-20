import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import webhooks
from app.core.config import settings
from app.crud import outreach as crud
from app.models.outreach import EmailSendLog
from app.tests.outreach.helpers import make_contact, make_subscriber

PATH = "/api/v1/webhooks/resend"
KEY = b"super-secret-signing-key"
SECRET = "whsec_" + base64.b64encode(KEY).decode()


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", SECRET)


def _signed(
    payload: dict[str, Any], *, key: bytes = KEY, ts: int | None = None
) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    stamp = str(ts if ts is not None else int(time.time()))
    msg_id = "msg_2abc"
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    headers = {
        "svix-id": msg_id,
        "svix-timestamp": stamp,
        "svix-signature": f"v1,bogus v1,{sig}",
        "content-type": "application/json",
    }
    return body, headers


def _post(client: TestClient, payload: dict[str, Any], **kw: Any):
    body, headers = _signed(payload, **kw)
    return client.post(PATH, content=body, headers=headers)


def test_bounce_suppresses_contact_and_subscriber_and_updates_the_send_log(
    client: TestClient, session: Session
) -> None:
    contact = make_contact(session, "hard@example.com", status="sent")
    sub = make_subscriber(session, "hard@example.com")
    crud.log_send(
        session,
        recipient_email="hard@example.com",
        email_type="intro",
        message_id="re_123",
        status="sent",
        consent_basis="one_time_dpdpa_sensitization",
        sent_at=datetime.now(UTC),
    )

    res = _post(
        client,
        {"type": "email.bounced", "data": {"email_id": "re_123", "to": ["Hard@Example.com"]}},
    )

    assert res.status_code == 200
    assert res.json() == {"received": True, "event": "email.bounced", "email": "hard@example.com"}
    session.refresh(contact)
    session.refresh(sub)
    assert contact.status == "bounced"
    assert sub.status == "bounced"
    log = session.exec(select(EmailSendLog)).one()
    assert log.status == "bounced"
    assert log.updated_at_attr is not None


def test_complaint_marks_both_tables_complained(client: TestClient, session: Session) -> None:
    contact = make_contact(session, "angry@example.com", status="sent")
    sub = make_subscriber(session, "angry@example.com")

    _post(client, {"type": "email.complained", "data": {"email": "angry@example.com"}})

    session.refresh(contact)
    session.refresh(sub)
    assert (contact.status, sub.status) == ("complained", "complained")


def test_event_without_a_logged_send_adds_a_webhook_row(
    client: TestClient, session: Session
) -> None:
    _post(
        client, {"type": "email.delivered", "data": {"email_id": "re_x", "to": ["a@example.com"]}}
    )

    log = session.exec(select(EmailSendLog)).one()
    assert (log.recipient_email, log.status, log.consent_basis, log.resend_message_id) == (
        "a@example.com",
        "delivered",
        "webhook_event",
        "re_x",
    )


def test_delivery_delayed_changes_nothing(client: TestClient, session: Session) -> None:
    contact = make_contact(session, "slow@example.com", status="sent")

    res = _post(client, {"type": "email.delivery_delayed", "data": {"to": ["slow@example.com"]}})

    assert res.status_code == 200
    session.refresh(contact)
    assert contact.status == "sent"
    assert session.exec(select(EmailSendLog)).all() == []


def test_rejects_a_bad_signature(client: TestClient) -> None:
    res = _post(client, {"type": "email.bounced", "data": {"to": ["a@example.com"]}}, key=b"wrong")

    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid signature"


def test_rejects_a_stale_timestamp(client: TestClient) -> None:
    res = _post(
        client,
        {"type": "email.bounced", "data": {"to": ["a@example.com"]}},
        ts=int(time.time()) - 3600,
    )

    assert res.status_code == 401


def test_rejects_missing_svix_headers(client: TestClient) -> None:
    res = client.post(PATH, content=b"{}")

    assert res.status_code == 401


def test_requires_an_email_in_the_payload(client: TestClient) -> None:
    res = _post(client, {"type": "email.bounced", "data": {}})

    assert res.status_code == 400
    assert res.json()["detail"] == "No email in payload"


def test_unconfigured_secret_is_a_server_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", "")

    res = _post(client, {"type": "email.bounced", "data": {"to": ["a@example.com"]}})

    assert res.status_code == 500
    assert res.json()["detail"] == "Webhook not configured"


def test_verify_svix_matches_the_reference_algorithm() -> None:
    body = b'{"type":"email.sent"}'
    ts = "1700000000"
    sig = base64.b64encode(
        hmac.new(KEY, b"id1." + ts.encode() + b"." + body, hashlib.sha256).digest()
    ).decode()

    assert webhooks.verify_svix(SECRET, body, "id1", ts, f"v1,{sig}", now=1700000100) is True
    assert (
        webhooks.verify_svix(SECRET, body + b" ", "id1", ts, f"v1,{sig}", now=1700000100) is False
    )
    assert webhooks.verify_svix(SECRET, body, "id1", ts, f"v2,{sig}", now=1700000100) is False
