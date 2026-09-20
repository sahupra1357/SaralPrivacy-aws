"""The founder alert, ported from `noticeLeadAlertTemplate` in `lib/email-templates.ts`."""

from typing import Any

import pytest

from app.api.routes.notices import send_notice_lead_alert
from app.core.config import settings


def test_subject_prefers_the_business_name_and_appends_the_sector(mock_email: Any) -> None:
    send_notice_lead_alert(
        email="owner@acme.in",
        business_name="Acme Pvt Ltd",
        sector="D2C Brand",
        readiness_score=64,
        export_type="pdf",
    )
    assert mock_email.last()["subject"] == "Notice Pack lead — Acme Pvt Ltd (D2C Brand)"


def test_subject_falls_back_to_the_email_and_omits_an_empty_sector(mock_email: Any) -> None:
    send_notice_lead_alert(
        email="owner@acme.in", business_name="", sector="", readiness_score=None, export_type=""
    )
    assert mock_email.last()["subject"] == "Notice Pack lead — owner@acme.in"


def test_body_carries_the_admin_alert_rows(mock_email: Any) -> None:
    send_notice_lead_alert(
        email="owner@acme.in",
        business_name="Acme Pvt Ltd",
        sector="D2C Brand",
        readiness_score=64,
        export_type="copy",
    )
    html = mock_email.last()["html"]
    assert "ADMIN ALERT" in html
    assert "Notice Pack generated" in html
    assert "Notice Pack Lead" in html
    assert "64 / 100" in html
    assert "Captured at the export gate of the Notice Pack Builder." in html


def test_missing_values_render_as_an_em_dash(mock_email: Any) -> None:
    send_notice_lead_alert(
        email="owner@acme.in", business_name="", sector="", readiness_score=None, export_type=""
    )
    html = mock_email.last()["html"]
    assert html.count("—") >= 4


def test_sent_from_the_noreply_address_to_the_admin(mock_email: Any) -> None:
    send_notice_lead_alert(
        email="a@b.in", business_name="B", sector="", readiness_score=1, export_type="pdf"
    )
    call = mock_email.last()
    assert call["to"] == [settings.ADMIN_EMAIL]
    assert call["from_"] == settings.EMAILS_FROM_NOREPLY


def test_nothing_is_sent_when_admin_email_is_unset(
    mock_email: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "ADMIN_EMAIL", "")
    send_notice_lead_alert(
        email="a@b.in", business_name="B", sector="", readiness_score=1, export_type="pdf"
    )
    assert mock_email.calls == []


def test_a_broken_mail_provider_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import email as email_service

    def boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(email_service, "send", boom)
    send_notice_lead_alert(
        email="a@b.in", business_name="B", sector="", readiness_score=1, export_type="pdf"
    )
