"""Email subject lines, band CTAs and the scorecard arithmetic, copied from
webapp/lib/email-templates.ts. Rendering only — nothing here touches the database.
"""

from typing import Any

import pytest

from app.api.routes import assessments as routes


@pytest.mark.parametrize(
    ("band", "expected"),
    [
        ("Not Started", "Your DPDPA Score: 12/100 — Here's exactly why and what to do first"),
        ("Early Stage", "Your DPDPA Score: 12/100 — Here's exactly why and what to do first"),
        (
            "Building Foundations",
            "Your DPDPA Score: 12/100 — You're building. Here's the path to 70+",
        ),
        ("Progressing Well", "Your DPDPA Score: 12/100 — You're building. Here's the path to 70+"),
        ("Operationally Strong", "Your DPDPA Score: 12/100 — Strong start. Here's what to protect"),
    ],
)
def test_survey_subject_per_band(band: str, expected: str) -> None:
    assert routes._survey_subject(band, 12) == expected


def test_survey_subject_prints_an_integral_score_without_a_decimal_point() -> None:
    assert "Score: 59/100" in routes._survey_subject("Early Stage", 59.0)


def test_scorecard_maps_six_engine_categories_onto_five_display_rows() -> None:
    rows = routes._scorecard(
        {
            "noticeConsent": 70,
            "accessControl": 40,
            "retentionDeletion": 31,
            "ownershipGovernance": 55,
            "vendorPartnerRisk": 46,
            "incidentReadiness": 20,
        }
    )

    assert [r["label"] for r in rows] == [
        "Notice & Consent",
        "Data Inventory & Storage",
        "Data Principal Rights & Control",
        "Ownership & Governance",
        "Incident & Operational Readiness",
    ]
    # (31 + 46) / 2 = 38.5, rounded half-up like JS Math.round
    assert rows[1]["score"] == 39
    assert [r["status"] for r in rows] == [
        "Strong",
        "Needs Work",
        "Developing",
        "Developing",
        "Needs Work",
    ]
    assert rows[0]["filled"] == 165  # round(70 * 2.36)


def test_scorecard_is_empty_without_category_scores() -> None:
    assert routes._scorecard(None) == []
    assert routes._scorecard({}) == []


def test_round_half_up_matches_javascript() -> None:
    assert routes._round_half_up(0.5) == 1
    assert routes._round_half_up(1.5) == 2
    assert routes._round_half_up(2.5) == 3  # Python's round() would give 2


def test_every_industry_report_type_has_a_checklist() -> None:
    assert set(routes.CHECKLISTS) == set(routes.INDUSTRY_REPORT_TYPES)
    for url, title in routes.CHECKLISTS.values():
        assert url.startswith("https://saralprivacy.com/templates/")
        assert title.endswith("Checklist")


def test_admin_alert_colours_the_badge_by_risk_level(mock_email: Any) -> None:
    for risk, colour in (("HIGH", "#E53E3E"), ("MEDIUM", "#E07B39"), ("LOW", "#2D9B6F")):
        routes._send_admin_alert(
            {
                "email": "a@example.com",
                "industry": "retail",
                "risk_level": risk,
                "applicability_score": 0,
                "maturity_score": None,
                "risk_score": 3,
                "urgency_score": 4,
                "overall_score": 5,
            }
        )
        assert colour in mock_email.last()["html"]


def test_admin_alert_prints_zero_scores_and_em_dashes_for_missing_ones(mock_email: Any) -> None:
    routes._send_admin_alert(
        {
            "email": "a@example.com",
            "industry": "retail",
            "risk_level": "LOW",
            "applicability_score": 0,
            "maturity_score": None,
            "risk_score": 3,
            "urgency_score": 4,
            "overall_score": 5,
        }
    )

    html = mock_email.last()["html"]
    assert ">0<" in html  # a zero score still prints
    assert ">—<" in html  # a missing one falls back to the em dash


def test_report_email_escapes_a_name_that_contains_markup(mock_email: Any) -> None:
    routes._send_survey_result(
        to="x@example.com",
        name="<script>alert(1)</script>",
        business_name="",
        score=40,
        band="Early Stage",
        summary="",
        recommendations=[],
        risk_flags=[],
        answer_summary=[],
        report_token="tok",
        category_scores=None,
        report_type="quick",
    )

    html = mock_email.last()["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_report_email_falls_back_to_the_early_stage_cta_for_an_unknown_band(
    mock_email: Any,
) -> None:
    routes._send_survey_result(
        to="x@example.com",
        name="",
        business_name="",
        score=50,
        band="Totally Unknown Band",
        summary="",
        recommendations=[],
        risk_flags=["flag one"],
        answer_summary=[],
        report_token="tok",
        category_scores=None,
        report_type="quick",
    )

    html = mock_email.last()["html"]
    assert "Download your free DPDPA Readiness Checklist" in html
    assert "Hi there" in html
    assert "flag one" in html
