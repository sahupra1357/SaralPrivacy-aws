from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import forms as routes
from app.models.forms import ConsentLog, Download, Subscriber
from app.tests.conftest import Recorder

PATH = "/api/v1/forms/white-paper"

PAYLOAD = {
    "fullName": "Neha Gupta",
    "workEmail": "neha@example.com",
    "companyName": "Gupta Foods",
    "industry": "Food & Beverage",
    "companySize": "11-50",
    "phone": "+919812345678",
    "language": "hi",
    "consentEmail": True,
    "consentPhone": False,
    "consentWebinars": True,
}


def test_white_paper_stores_the_download_and_returns_the_language_pdf(
    client: TestClient, session: Session
) -> None:
    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["language"] == "hi"
    assert body["partial"] is False
    assert body["message"] == "Download ready."
    assert body["downloadUrl"].endswith("dpdpa-guide-hi.pdf")

    row = session.exec(select(Download)).one()
    assert row.name == "Neha Gupta"
    assert row.company == "Gupta Foods"
    assert row.language == "hi"
    assert row.privacy_version == "1.0.0"
    assert row.consent_email is True
    assert row.consent_phone is False
    assert row.consent_webinars is True
    assert row.downloaded_at is not None


def test_white_paper_falls_back_to_english_for_an_unknown_language(
    client: TestClient, session: Session
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "language": "klingon"})

    assert res.json()["language"] == "en"
    assert res.json()["downloadUrl"].endswith("dpdpa-guide-en.pdf")
    assert session.exec(select(Download)).one().language == "en"


def test_white_paper_writes_one_consent_row_per_checked_box(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json=PAYLOAD)

    rows = session.exec(select(ConsentLog)).all()
    per_box = [r for r in rows if r.source == "download"]
    assert {r.consent_type for r in per_box} == {"email_marketing", "webinars"}
    assert all(r.privacy_version == "1.0.0" for r in rows)
    # upsert_subscriber also logs the subscription itself, as lib/subscribers.ts intended.
    # (In production that row never landed: the subscriber insert above it failed on a
    # non-existent `source` column. The rewrite fixes that insert.)
    assert [(r.source, r.consent_type) for r in rows if r.source != "download"] == [
        ("whitepaper_form", "email_marketing")
    ]


def test_white_paper_subscribes_only_with_email_consent(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json={**PAYLOAD, "consentEmail": False})

    assert session.exec(select(Subscriber)).first() is None


def test_white_paper_creates_a_subscriber_with_email_consent(
    client: TestClient, session: Session
) -> None:
    client.post(PATH, json=PAYLOAD)

    sub = session.exec(select(Subscriber)).one()
    assert sub.email == "neha@example.com"
    assert sub.industry == "Food & Beverage"
    assert sub.status == "active"


def test_white_paper_alerts_the_admin(client: TestClient, mock_email: Recorder) -> None:
    client.post(PATH, json=PAYLOAD)

    alert = next(c for c in mock_email.calls if c["subject"].startswith("White Paper Downloaded"))
    assert alert["subject"] == "White Paper Downloaded — Neha Gupta from Gupta Foods"
    assert "Yes — follow up permitted" in alert["html"]


def test_white_paper_requires_all_five_fields(client: TestClient, session: Session) -> None:
    res = client.post(PATH, json={**PAYLOAD, "companySize": ""})

    assert res.status_code == 400
    assert res.json()["detail"] == "Required fields are missing."
    assert session.exec(select(Download)).first() is None


def test_white_paper_honeypot_drops_silently(
    client: TestClient, session: Session, mock_email: Recorder
) -> None:
    res = client.post(PATH, json={**PAYLOAD, "hp_url": "bot"})

    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert session.exec(select(Download)).first() is None
    assert mock_email.calls == []


def test_white_paper_rate_limits_after_five_requests(client: TestClient) -> None:
    for _ in range(5):
        assert client.post(PATH, json=PAYLOAD).status_code == 200

    res = client.post(PATH, json=PAYLOAD)

    assert res.status_code == 429
    assert res.json()["detail"] == "Too many requests. Please try again later."


def test_guide_language_resolution() -> None:
    assert routes.resolve_language("ta") == "ta"
    assert routes.resolve_language(None) == "en"
    assert routes.resolve_language("xx") == "en"
    assert routes.guide_pdf_url("te").endswith("dpdpa-guide-te.pdf")
