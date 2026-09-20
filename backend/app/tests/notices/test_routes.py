"""Routes ported from `app/api/notice/{capture,events,pdf}/route.ts`.

One success test per route plus one per distinct guard (honeypot, validation, rate
limit, render failure), asserting the exact strings from
`docs/build/inventory/notices.md`.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core import ratelimit
from app.models.notices import NoticeCapture, NoticeEvent

CAPTURE = "/api/v1/notices/capture"
EVENTS = "/api/v1/notices/events"
PDF = "/api/v1/notices/pdf"

RATE_LIMITED = "Too many requests. Please wait a moment and try again."


def _ip(addr: str) -> dict[str, str]:
    """Distinct rate-limit bucket per test (client_ip reads the rightmost XFF hop)."""
    return {"x-forwarded-for": addr}


# ── POST /notices/capture ────────────────────────────────────────────────────
def test_capture_stores_the_lead_and_alerts_the_founder(
    client: TestClient, session: Session, mock_email: Any
) -> None:
    res = client.post(
        CAPTURE,
        json={
            "email": "owner@acme.in",
            "business_name": "Acme Pvt Ltd",
            "sector": "CA Firm",
            "readiness_score": 72,
            "export_type": "pdf",
            "consent": True,
        },
        headers={**_ip("9.9.9.1"), "x-vercel-ip-city": "New%20Delhi", "x-vercel-ip-country": "IN"},
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}

    row = session.exec(select(NoticeCapture).where(NoticeCapture.email == "owner@acme.in")).one()
    assert row.name == "owner"  # falls back to the local part of the email
    assert row.business_name == "Acme Pvt Ltd"
    assert row.readiness_score == 72
    assert row.export_type == "pdf"
    assert row.source == "notice-generator"
    assert row.consent is True
    assert row.ip_address == "9.9.9.1"
    assert row.city == "New Delhi"
    assert row.country == "IN"
    assert row.created_at_attr is not None and row.created_at_attr.endswith("Z")

    sent = mock_email.last()
    assert sent["subject"] == "Notice Pack lead — Acme Pvt Ltd (CA Firm)"
    assert "Notice Pack generated" in sent["html"]
    assert "72 / 100" in sent["html"]


def test_capture_defaults_score_and_export_type_when_absent(
    client: TestClient, session: Session
) -> None:
    res = client.post(CAPTURE, json={"email": "a@b.in"}, headers=_ip("9.9.9.2"))
    assert res.status_code == 200
    row = session.exec(select(NoticeCapture).where(NoticeCapture.email == "a@b.in")).one()
    assert row.readiness_score == 0
    assert row.export_type == ""
    assert row.consent is False


def test_capture_honeypot_returns_success_and_stores_nothing(
    client: TestClient, session: Session, mock_email: Any
) -> None:
    res = client.post(
        CAPTURE,
        json={"email": "bot@spam.in", "hp_url": "http://spam"},
        headers=_ip("9.9.9.3"),
    )
    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert (
        session.exec(select(NoticeCapture).where(NoticeCapture.email == "bot@spam.in")).first()
        is None
    )
    assert mock_email.calls == []


def test_capture_rejects_a_bad_email_with_the_exact_message(client: TestClient) -> None:
    res = client.post(CAPTURE, json={"email": "not-an-email"}, headers=_ip("9.9.9.4"))
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid email or fields."


def test_capture_rejects_an_out_of_range_score(client: TestClient) -> None:
    res = client.post(
        CAPTURE, json={"email": "a@b.in", "readiness_score": 101}, headers=_ip("9.9.9.5")
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid email or fields."


def test_capture_rejects_an_unknown_export_type(client: TestClient) -> None:
    res = client.post(
        CAPTURE, json={"email": "a@b.in", "export_type": "zip"}, headers=_ip("9.9.9.6")
    )
    assert res.status_code == 400


def test_capture_rate_limits_after_eight_in_a_minute(client: TestClient) -> None:
    for _ in range(8):
        assert (
            client.post(CAPTURE, json={"email": "a@b.in"}, headers=_ip("9.9.9.7")).status_code
            == 200
        )
    blocked = client.post(CAPTURE, json={"email": "a@b.in"}, headers=_ip("9.9.9.7"))
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == RATE_LIMITED
    assert int(blocked.headers["retry-after"]) >= 1


def test_capture_answers_500_with_the_exact_message_when_the_insert_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.crud import notices as crud

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(crud, "create_capture", boom)
    res = client.post(CAPTURE, json={"email": "a@b.in"}, headers=_ip("9.9.9.8"))
    assert res.status_code == 500
    assert res.json()["detail"] == "An unexpected error occurred. Please try again."


def test_capture_survives_a_failing_founder_alert(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import email as email_service

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "send", boom)
    res = client.post(CAPTURE, json={"email": "resilient@acme.in"}, headers=_ip("9.9.9.9"))
    assert res.status_code == 200
    assert session.exec(
        select(NoticeCapture).where(NoticeCapture.email == "resilient@acme.in")
    ).one()


# ── POST /notices/events ─────────────────────────────────────────────────────
def test_events_stores_a_known_event_with_a_compact_payload(
    client: TestClient, session: Session
) -> None:
    res = client.post(
        EVENTS,
        json={"name": "notice_pdf_downloaded", "session_id": "sid-1", "sector": "d2c", "score": 81},
        headers=_ip("8.8.8.1"),
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    row = session.exec(select(NoticeEvent).where(NoticeEvent.session_id == "sid-1")).one()
    assert row.name == "notice_pdf_downloaded"
    assert row.payload == '{"sector":"d2c","score":81}'
    assert json.loads(row.payload) == {"sector": "d2c", "score": 81}


def test_events_omits_absent_optional_fields_from_the_payload(
    client: TestClient, session: Session
) -> None:
    client.post(
        EVENTS,
        json={"name": "notice_builder_started", "session_id": "sid-2"},
        headers=_ip("8.8.8.2"),
    )
    row = session.exec(select(NoticeEvent).where(NoticeEvent.session_id == "sid-2")).one()
    assert row.payload == "{}"


def test_events_drops_an_unknown_event_name_without_erroring(
    client: TestClient, session: Session
) -> None:
    res = client.post(
        EVENTS, json={"name": "definitely_not_ours", "session_id": "sid-3"}, headers=_ip("8.8.8.3")
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert (
        session.exec(select(NoticeEvent).where(NoticeEvent.session_id == "sid-3")).first() is None
    )


def test_events_drops_a_malformed_body_without_erroring(client: TestClient) -> None:
    res = client.post(
        EVENTS, content=b"not json", headers={**_ip("8.8.8.4"), "content-type": "application/json"}
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


def test_events_drops_floods_silently_instead_of_429(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.routes import notices

    monkeypatch.setattr(
        notices, "hit", lambda *_a, **_kw: ratelimit.RateLimitResult(ok=False, retry_after=30)
    )
    res = client.post(
        EVENTS, json={"name": "dsar_cta_clicked", "session_id": "sid-5"}, headers=_ip("8.8.8.5")
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}
    assert (
        session.exec(select(NoticeEvent).where(NoticeEvent.session_id == "sid-5")).first() is None
    )


def test_events_still_answers_ok_when_the_insert_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.crud import notices as crud

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(crud, "create_event", boom)
    res = client.post(EVENTS, json={"name": "mini_notice_copied"}, headers=_ip("8.8.8.6"))
    assert res.status_code == 200
    assert res.json() == {"ok": True}


# ── POST /notices/pdf ────────────────────────────────────────────────────────
def _pdf_body(**kw: object) -> dict[str, Any]:
    body: dict[str, Any] = {
        "org": "Acme Pvt Ltd",
        "lang": "en",
        "effIso": "2026-09-18T06:30:00.000Z",
    }
    body.update(kw)
    return body


def test_pdf_returns_an_attachment_with_the_slugged_filename(client: TestClient) -> None:
    res = client.post(PDF, json=_pdf_body(), headers=_ip("7.7.7.1"))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert (
        res.headers["content-disposition"]
        == 'attachment; filename="dpdpa-privacy-notice-acme-pvt-ltd.pdf"'
    )
    assert res.headers["cache-control"] == "no-store"
    assert res.content.startswith(b"%PDF")


def test_pdf_prints_the_shared_document_with_the_branded_header_and_footer(
    client: TestClient, mock_pdf: Any
) -> None:
    client.post(PDF, json=_pdf_body(sector="clinic"), headers=_ip("7.7.7.2"))
    call = mock_pdf.last()
    assert call["html"].startswith('<!doctype html><html lang="en">')
    assert "Acme Pvt Ltd is a Clinic / Diagnostic Lab." in call["html"]
    assert "Effective date: 18 September 2026" in call["html"]
    assert "DPDPA Privacy Notice" in call["header_html"]
    assert "not legal advice" in call["footer_html"]
    assert call["format"] == "A4"
    assert call["margins"] == {"top": "18mm", "bottom": "20mm", "left": "16mm", "right": "16mm"}


def test_pdf_renders_hindi_when_lang_is_hi(client: TestClient, mock_pdf: Any) -> None:
    client.post(PDF, json=_pdf_body(lang="hi"), headers=_ip("7.7.7.3"))
    html = mock_pdf.last()["html"]
    assert '<html lang="hi">' in html
    assert "गोपनीयता सूचना — Acme Pvt Ltd" in html
    assert "प्रभावी तिथि: 18 सितंबर 2026" in html


def test_pdf_filename_falls_back_when_the_org_is_blank(client: TestClient) -> None:
    res = client.post(PDF, json=_pdf_body(org=""), headers=_ip("7.7.7.4"))
    assert (
        res.headers["content-disposition"]
        == 'attachment; filename="dpdpa-privacy-notice-your-business.pdf"'
    )


def test_pdf_rejects_a_non_json_body(client: TestClient) -> None:
    res = client.post(
        PDF, content=b"<<<", headers={**_ip("7.7.7.5"), "content-type": "application/json"}
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid request body."


def test_pdf_rejects_an_oversized_field(client: TestClient) -> None:
    res = client.post(PDF, json=_pdf_body(org="x" * 161), headers=_ip("7.7.7.6"))
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid notice data."


def test_pdf_rejects_an_unknown_language(client: TestClient) -> None:
    res = client.post(PDF, json=_pdf_body(lang="fr"), headers=_ip("7.7.7.7"))
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid notice data."


def test_pdf_rejects_too_many_data_categories(client: TestClient) -> None:
    res = client.post(
        PDF, json=_pdf_body(data=[f"d{i}" for i in range(81)]), headers=_ip("7.7.7.8")
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid notice data."


def test_pdf_rate_limits_after_six_in_a_minute(client: TestClient) -> None:
    for _ in range(6):
        assert client.post(PDF, json=_pdf_body(), headers=_ip("7.7.7.9")).status_code == 200
    blocked = client.post(PDF, json=_pdf_body(), headers=_ip("7.7.7.9"))
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == RATE_LIMITED
    assert int(blocked.headers["retry-after"]) >= 1


def test_pdf_answers_the_print_fallback_message_when_the_render_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import pdf as pdf_service

    def boom(*_a: object, **_kw: object) -> bytes:
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(pdf_service, "render_html", boom)
    res = client.post(PDF, json=_pdf_body(), headers=_ip("7.7.7.10"))
    assert res.status_code == 500
    assert res.json()["detail"] == "Could not generate the PDF. Please try the print fallback."
