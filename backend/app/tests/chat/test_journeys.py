"""Journey detection and the untrusted-state sanitiser."""

from app.services.chat import site_routing
from app.services.chat.journeys import (
    JOURNEYS,
    create_initial_state,
    detect_journey,
    journey_by_id,
    sanitize_state,
)


def test_every_journey_completion_url_is_linkable() -> None:
    for j in JOURNEYS:
        assert site_routing.is_valid_citation(j.completion_url), j.id


def test_detect_journey_matches_entry_keywords() -> None:
    assert detect_journey("Does DPDPA apply to my firm?") == "J1"
    assert detect_journey("where do i start with all this") == "J3"
    assert detect_journey("do I need consent for marketing?") == "J4"


def test_detect_journey_stays_in_the_active_journey_when_the_turn_does_not_pivot() -> None:
    assert detect_journey("and what about employees", "J4") == "J4"
    assert detect_journey("and what about employees") is None


def test_journey_by_id_returns_the_named_journey() -> None:
    assert journey_by_id("J5").completion_url == "/tools/dpdpa-privacy-notice-generator"


def test_sanitize_state_returns_a_fresh_state_for_junk() -> None:
    for junk in (None, "nope", 7, []):
        state = sanitize_state(junk, "sess-1", "/learn")
        assert state.session_id == "sess-1"
        assert state.entry_page_url == "/learn"
        assert state.facts_confirmed == {}
        assert state.handoff_offered is False


def test_sanitize_state_clamps_every_untrusted_field() -> None:
    state = sanitize_state(
        {
            "factsConfirmed": {f"k{i}": "v" * 300 for i in range(40)},
            "pagesShown": ["/learn"] * 60 + [123],
            "messageCount": 99999,
            "consecutiveRefusals": 9999,
            "stalledSlotTurns": -5,
            "journey": "J9",
            "userType": "hacker",
            "handoffOffered": "yes",
            "consentToContact": "yes",
        },
        "sess-1",
        "/",
    )
    assert len(state.facts_confirmed) == 20
    assert all(len(v) == 200 for v in state.facts_confirmed.values())
    assert len(state.pages_shown) == 30
    assert state.message_count == 500
    assert state.consecutive_refusals == 10
    assert state.stalled_slot_turns == 0
    assert state.journey is None
    assert state.user_type is None
    # Only a literal true counts — a truthy string must not grant consent or close the door.
    assert state.handoff_offered is False
    assert state.consent_to_contact is False


def test_sanitize_state_keeps_valid_values() -> None:
    state = sanitize_state(
        {
            "journey": "J2",
            "userType": "owner",
            "industry": "ca-firms",
            "factsConfirmed": {"hasNotice": "yes"},
            "pagesShown": ["/learn/consent"],
            "messageCount": 3,
            "consecutiveRefusals": 1,
            "handoffOffered": True,
            "consentToContact": True,
        },
        "sess-1",
        "/",
    )
    assert state.journey == "J2"
    assert state.user_type == "owner"
    assert state.industry == "ca-firms"
    assert state.facts_confirmed == {"hasNotice": "yes"}
    assert state.pages_shown == ["/learn/consent"]
    assert state.message_count == 3
    assert state.consecutive_refusals == 1
    assert state.handoff_offered is True
    assert state.consent_to_contact is True


def test_create_initial_state_starts_every_counter_at_zero() -> None:
    state = create_initial_state("s", "/")
    assert (state.message_count, state.consecutive_refusals, state.stalled_slot_turns) == (0, 0, 0)
