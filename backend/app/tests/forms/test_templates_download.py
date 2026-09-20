from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import forms as routes
from app.core.config import settings
from app.models.forms import Subscriber, TemplateDownload
from app.services import email as email_service
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/templates/download"

PAYLOAD = {
    "email": "ravi@example.com",
    "contactPersonName": "Ravi Kumar",
    "businessName": "Kumar Legal",
    "templateSelected": "privacy-notice",
    "phoneNumber": "+919876543210",
    "consentContact": False,
    "consentBriefings": False,
}


@pytest.fixture(autouse=True)
def _no_twilio(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twilio stays optional; without credentials the WhatsApp step degrades quietly."""
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "")


def test_templates_download_emails_the_template_and_returns_the_public_url(
    client: TestClient, session: Session, mock_email: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com")

    res = client.post(PATH, json=PAYLOAD, headers={"referer": "https://saralprivacy.com/resources"})

    assert res.status_code == 200
    assert res.json() == {
        "success": True,
        "message": "Template sent successfully",
        "downloadUrl": "https://saralprivacy.com/templates/privacy-notice.docx",
        "email": True,
        "whatsapp": False,
    }

    sent = mock_email.last()
    assert sent["to"] == ["ravi@example.com"]
    assert sent["subject"] == 'Your "Privacy Notice Template" is ready — download now'
    assert "https://saralprivacy.com/templates/privacy-notice.docx" in sent["html"]
    assert "Hi Ravi Kumar," in sent["html"]

    row = session.exec(select(TemplateDownload)).one()
    assert row.template_name == "Privacy Notice Template"
    assert row.phone == "+919876543210"
    assert row.source == "https://saralprivacy.com/resources"


def test_templates_download_falls_back_to_direct_when_there_is_no_referer(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json=PAYLOAD)

    assert session.exec(select(TemplateDownload)).one().source == "direct"


def test_templates_download_subscribes_when_briefings_consent_is_given(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    client.post(PATH, json={**PAYLOAD, "consentBriefings": True})

    assert session.exec(select(Subscriber)).one().email == "ravi@example.com"
    assert "expect your first one tomorrow morning" in mock_email.last()["html"]


def test_templates_download_reports_every_field_error_with_the_form_messages(
    client: TestClient,
) -> None:
    res = client.post(
        PATH,
        json={
            "email": "not-an-email",
            "contactPersonName": "R",
            "businessName": "K",
            "templateSelected": "nope",
            "phoneNumber": "12345",
            "consentContact": False,
            "consentBriefings": False,
        },
    )

    assert res.status_code == 400
    body = res.json()
    assert body["message"] == "Invalid form data"
    assert body["errors"] == {
        "email": ["Please enter a valid email address"],
        "contactPersonName": ["Name must be at least 2 characters"],
        "businessName": ["Business name must be at least 2 characters"],
        "templateSelected": ["Please select a template"],
        "phoneNumber": ["Please enter a valid Indian phone number (+91 format)"],
    }


def test_templates_download_rejects_an_overlong_name(client: TestClient) -> None:
    res = client.post(PATH, json={**PAYLOAD, "contactPersonName": "R" * 101})

    assert res.status_code == 400
    assert res.json()["errors"]["contactPersonName"] == ["Name too long"]


def test_templates_download_honeypot_drops_silently(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "hp_url": "bot"})

    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert session.exec(select(TemplateDownload)).first() is None
    assert mock_email.calls == []


def test_templates_download_rate_limits_after_five_requests(client: TestClient) -> None:
    for _ in range(5):
        assert client.post(PATH, json=PAYLOAD).status_code == 200

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please try again later."


def test_templates_download_fails_the_request_when_the_email_cannot_be_sent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "send", boom)

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 500
    assert res.json() == {"message": "Failed to send template email. Please try again."}


def test_whatsapp_is_skipped_when_twilio_is_not_configured() -> None:
    assert (
        routes.send_template_whatsapp(
            phone_number="+919876543210",
            contact_person_name="Ravi",
            template_name="Privacy Notice Template",
            download_url="https://saralprivacy.com/templates/privacy-notice.docx",
        )
        is False
    )


def test_whatsapp_posts_to_twilio_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+14155238886")
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 201

        def json(self) -> dict[str, str]:
            return {"sid": "SM1"}

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(routes.httpx, "post", fake_post)

    ok = routes.send_template_whatsapp(
        phone_number="+919876543210",
        contact_person_name="Ravi",
        template_name="Privacy Notice Template",
        download_url="https://saralprivacy.com/templates/privacy-notice.docx",
    )

    assert ok is True
    assert captured["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert captured["data"]["From"] == "whatsapp:+14155238886"
    assert captured["data"]["To"] == "whatsapp:+919876543210"
    assert captured["data"]["Body"].startswith("Hi Ravi! 👋")
    assert "*Privacy Notice Template*" in captured["data"]["Body"]


def test_whatsapp_returns_false_when_twilio_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+14155238886")

    class FakeResponse:
        status_code = 400

        def json(self) -> dict[str, str]:
            return {"message": "not a whatsapp number"}

    monkeypatch.setattr(routes.httpx, "post", lambda url, **kw: FakeResponse())

    assert (
        routes.send_template_whatsapp(
            phone_number="+911111111111",
            contact_person_name="Ravi",
            template_name="Privacy Notice Template",
            download_url="https://example.com/x.docx",
        )
        is False
    )


def test_every_template_id_maps_to_a_name_and_a_public_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "NEXT_PUBLIC_SITE_URL", "https://saralprivacy.com/")

    for template_id in routes.TEMPLATE_OPTIONS:
        assert routes.TEMPLATE_NAMES[template_id]
        url = routes.template_download_url(template_id)
        assert url.startswith("https://saralprivacy.com/templates/")
        assert url.endswith((".docx", ".xlsx"))


def test_phone_numbers_are_normalised_to_e164() -> None:
    assert routes.format_phone_number("09876543210") == "+919876543210"
    assert routes.format_phone_number("not a phone") == "not a phone"
    assert routes.valid_indian_phone_number("9876543210") is True
    assert routes.valid_indian_phone_number("12345") is False
