import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import forms as routes
from app.core.config import settings
from app.models.forms import Subscriber

PATH = "/api/v1/forms/subscribers/unsubscribe"
EMAIL = "leela@example.com"


@pytest.fixture
def subscriber(session: Session) -> Subscriber:
    row = Subscriber(name="Leela", email=EMAIL, status="active", consent_source="manual")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _sign(email: str, secret: str) -> str:
    return hmac.new(secret.encode(), email.encode(), hashlib.sha256).hexdigest()


def test_unsubscribe_soft_deletes_the_row(
    client: TestClient, session: Session, subscriber: Subscriber
) -> None:
    res = client.post(PATH, json={"email": "  Leela@Example.com "})

    assert res.status_code == 200
    assert res.json() == {"success": True}

    session.refresh(subscriber)
    assert subscriber.status == "unsubscribed"
    assert subscriber.unsubscribed_at is not None


def test_unsubscribe_reports_already_removed_for_an_unknown_email(client: TestClient) -> None:
    res = client.post(PATH, json={"email": "nobody@example.com"})

    assert res.status_code == 200
    assert res.json() == {"success": True, "already_removed": True}


def test_unsubscribe_requires_an_email(client: TestClient) -> None:
    res = client.post(PATH, json={})

    assert res.status_code == 400
    assert res.json()["detail"] == "email required"


def test_unsubscribe_throttles_unsigned_requests(
    client: TestClient, subscriber: Subscriber
) -> None:
    assert subscriber.status == "active"

    for _ in range(5):
        assert client.post(PATH, json={"email": EMAIL}).status_code == 200

    res = client.post(PATH, json={"email": EMAIL})

    assert res.status_code == 429
    assert (
        res.json()["detail"]
        == "Too many requests. Try again later or email privacy@saralprivacy.com."
    )


def test_unsubscribe_honours_a_signed_link_past_the_throttle(
    client: TestClient, session: Session, subscriber: Subscriber, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "s3cret")
    sig = _sign(EMAIL, "s3cret")

    for _ in range(8):
        res = client.post(PATH, json={"email": EMAIL, "sig": sig})
        assert res.status_code == 200

    session.refresh(subscriber)
    assert subscriber.status == "unsubscribed"


def test_verify_unsubscribe_sig_is_false_without_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "")

    assert routes.verify_unsubscribe_sig(EMAIL, _sign(EMAIL, "s3cret")) is False


def test_verify_unsubscribe_sig_rejects_a_wrong_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "EMAIL_LINK_SECRET", "s3cret")

    assert routes.verify_unsubscribe_sig(EMAIL, _sign("someone@else.com", "s3cret")) is False
    assert routes.verify_unsubscribe_sig(EMAIL, None) is False
    assert routes.verify_unsubscribe_sig(EMAIL, _sign(EMAIL, "s3cret")) is True


def test_unsubscribe_does_not_create_a_row_for_an_unknown_email(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json={"email": "ghost@example.com"})

    assert session.exec(select(Subscriber)).first() is None
