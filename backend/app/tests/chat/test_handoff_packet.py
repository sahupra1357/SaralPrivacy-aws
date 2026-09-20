"""Handoff validation and packet assembly. Ported from frontend/lib/chat/handoff.test.ts."""

from datetime import UTC, datetime

from app.services.chat.handoff import (
    build_handoff_packet,
    is_escalation_reason,
    iso_ms,
    validate_handoff_input,
)
from app.services.chat.journeys import create_initial_state


def test_consent_must_be_literally_true() -> None:
    for consent in (None, "true", 1, False):
        result = validate_handoff_input({"name": "Asha", "email": "a@b.co", "consent": consent})
        assert result.ok is False
        assert result.error == "Consent is required."


def test_name_is_required() -> None:
    result = validate_handoff_input({"name": "   ", "email": "a@b.co", "consent": True})
    assert result.error == "Please add your name."


def test_email_must_look_like_an_address() -> None:
    for email in ("not-an-email", "a@b", "", "a b@c.co"):
        result = validate_handoff_input({"name": "Asha", "email": email, "consent": True})
        assert result.error == "Please check the email address."


def test_a_valid_submission_returns_a_trimmed_contact() -> None:
    result = validate_handoff_input({"name": "  Asha  ", "email": " a@b.co ", "consent": True})
    assert result.ok is True
    assert result.contact is not None
    assert result.contact.name == "Asha"
    assert result.contact.email == "a@b.co"


def test_escalation_reasons_are_an_allowlist() -> None:
    assert is_escalation_reason("explicit_ask") is True
    assert is_escalation_reason("negative_feedback") is True
    assert is_escalation_reason("anything_else") is False
    assert is_escalation_reason(None) is False


def test_packet_redacts_pii_before_it_can_reach_the_email() -> None:
    state = create_initial_state("sess-1", "/")
    state.message_count = 4
    packet = build_handoff_packet(
        state=state,
        page_url="/learn/consent",
        reason="explicit_ask",
        last_user_message="call me on 9876543210 or mail a@b.com",
    )
    assert "9876543210" not in packet.unresolved_question
    assert "a@b.com" not in packet.unresolved_question
    assert "[phone]" in packet.unresolved_question
    assert "[email]" in packet.unresolved_question


def test_packet_escapes_markup_that_would_otherwise_be_live_html_in_an_alert() -> None:
    state = create_initial_state("sess-1", "/")
    state.facts_confirmed = {"note": "<img src=x onerror=alert(1)>"}
    packet = build_handoff_packet(
        state=state, page_url="/", reason="explicit_ask", last_user_message="<b>hi</b>"
    )
    assert "<img" not in packet.summary
    assert "&lt;img" in packet.summary
    assert packet.unresolved_question == "&lt;b&gt;hi&lt;/b&gt;"


def test_packet_drops_invented_page_paths_and_industry_slugs() -> None:
    state = create_initial_state("sess-1", "/")
    state.pages_shown = ["/learn/consent", "https://evil.example", "javascript:alert(1)"]
    state.industry = "Not A Slug!"
    packet = build_handoff_packet(
        state=state, page_url="/", reason="explicit_ask", last_user_message=""
    )
    assert packet.sources_shown == ["/learn/consent"]
    assert packet.industry is None


def test_packet_names_the_journey_as_the_intent() -> None:
    state = create_initial_state("sess-1", "/")
    state.journey = "J4"
    packet = build_handoff_packet(
        state=state, page_url="/", reason="journey_stalled", last_user_message="help"
    )
    assert packet.intent == "Do I need consent for this?"
    assert packet.journey == "J4"
    assert packet.reason == "journey_stalled"
    assert packet.consent_to_contact is True


def test_packet_without_a_journey_says_general_enquiry() -> None:
    packet = build_handoff_packet(
        state=create_initial_state("sess-1", "/"),
        page_url="/",
        reason="explicit_ask",
        last_user_message="",
    )
    assert packet.intent == "general enquiry"


def test_timestamp_matches_the_javascript_iso_format() -> None:
    stamped = build_handoff_packet(
        state=create_initial_state("s", "/"),
        page_url="/",
        reason="explicit_ask",
        last_user_message="",
        now=datetime(2026, 9, 18, 4, 5, 6, 789123, tzinfo=UTC),
    )
    assert stamped.ts == "2026-09-18T04:05:06.789Z"
    assert iso_ms(datetime(2026, 1, 2, 3, 4, 5, 0, tzinfo=UTC)) == "2026-01-02T03:04:05.000Z"
