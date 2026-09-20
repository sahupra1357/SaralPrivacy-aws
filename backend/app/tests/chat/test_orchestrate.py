"""The deterministic brain around the model. Ported from
frontend/lib/chat/{orchestrate,escalation}.test.ts.

Every assertion here is about something the model is *not* allowed to author: the URLs,
the confidence, the refusal and the human door.
"""

import pytest

from app.services.chat import site_routing
from app.services.chat.journeys import ChatSessionState, create_initial_state
from app.services.chat.orchestrate import (
    DISCLAIMER,
    build_actions,
    build_citations,
    build_followups,
    build_grounding_block,
    build_meta,
    detect_industry,
    guarded_meta,
    mentions_word,
    plan_turn,
    router_rescue,
)


def _state(**kw: object) -> ChatSessionState:
    state = create_initial_state("sess-1", "/")
    for k, v in kw.items():
        setattr(state, k, v)
    return state


def test_mentions_word_is_whole_word_only() -> None:
    # "ats" inside "whatsapp" classified every WhatsApp question as a recruitment agency.
    assert mentions_word(" do we need consent for whatsapp ", "ats") is False
    assert mentions_word(" we run an ats for clients ", "ats") is True


def test_detect_industry_from_the_message() -> None:
    assert detect_industry("we are a chartered accountant practice", _state()) == "ca-firms"


def test_detect_industry_prefers_confirmed_state() -> None:
    assert detect_industry("we run a gym", _state(industry="pharmacies")) == "pharmacies"


def test_detect_industry_falls_back_to_the_current_page() -> None:
    assert (
        detect_industry("what applies here", _state(), "/industries/hotels-travel")
        == "hotels-travel"
    )


def test_detect_industry_returns_none_when_nothing_matches() -> None:
    assert detect_industry("what is dpdpa", _state()) is None


def test_router_rescue_returns_tier_1_first() -> None:
    routes = router_rescue("what is dpdpa")
    assert routes
    assert routes[0].tier == 1


def test_a_covered_question_is_not_refused() -> None:
    plan = plan_turn("What must my privacy notice say?", _state())
    assert plan.refuse is False
    meta = build_meta(plan)
    assert meta.refusal is False
    assert meta.disclaimer == DISCLAIMER
    assert meta.citations
    assert all(site_routing.is_valid_citation(c.url) for c in meta.citations)
    assert all(site_routing.is_valid_citation(a.url) for a in meta.actions)


def test_an_off_corpus_question_refuses_with_the_escape_hatches() -> None:
    plan = plan_turn("how do I bake sourdough bread at home", _state())
    assert plan.refuse is True
    meta = build_meta(plan)
    assert meta.refusal is True
    assert meta.citations == []
    assert meta.confidence == "low"
    assert meta.animation_state == "unsure"
    assert [a.url for a in meta.actions] == ["/faq", "/learn", "/contact"]
    assert meta.suggested_followups == ["What is DPDPA?", "Does DPDPA apply to me?"]


def test_a_router_trigger_rescues_a_low_scoring_turn() -> None:
    plan = plan_turn("what is dpdpa", _state())
    assert plan.refuse is False
    assert build_meta(plan).confidence == "high"


def test_a_platform_question_never_refuses_and_opens_the_tour() -> None:
    plan = plan_turn("help me navigate this site", _state())
    assert plan.nav_intent is True
    assert plan.refuse is False
    meta = build_meta(plan)
    assert [a.url for a in meta.actions][:1] == ["/discovery"]
    assert meta.suggested_followups[0] == "What tools does SaralPrivacy offer?"


def test_escalation_intent_puts_the_human_door_first() -> None:
    plan = plan_turn("I want to talk to a human", _state())
    assert plan.escalate is True
    meta = build_meta(plan)
    assert meta.actions[0].label == "Talk to a human — Contact SaralPrivacy"
    assert meta.escalation_reason == "explicit_ask"


def test_the_door_is_never_offered_twice_in_one_session() -> None:
    plan = plan_turn("I want to talk to a human", _state(handoff_offered=True))
    assert plan.escalation_reason is None
    assert build_meta(plan).escalation_reason is None


def test_two_refusals_running_open_the_door() -> None:
    plan = plan_turn("how do I bake sourdough bread", _state(consecutive_refusals=1))
    assert plan.refuse is True
    assert plan.escalation_reason == "repeat_refusal"


def test_a_stalled_journey_opens_the_door() -> None:
    plan = plan_turn("does dpdpa apply to me", _state(stalled_slot_turns=1))
    assert plan.journey == "J1"
    assert plan.escalation_reason == "journey_stalled"


def test_a_journey_with_every_slot_filled_does_not_stall() -> None:
    state = _state(
        stalled_slot_turns=1,
        facts_confirmed={
            "orgType": "pvt ltd",
            "handlesDigitalData": "yes",
            "whoseData": "customers",
        },
    )
    plan = plan_turn("does dpdpa apply to me", state)
    assert plan.escalation_reason is None


def test_pii_in_the_message_raises_the_warning_flag() -> None:
    plan = plan_turn("my pan is ABCDE1234F, does dpdpa apply to me?", _state())
    assert plan.pii_warning is True
    assert build_meta(plan).pii_warning is True


def test_citations_are_capped_at_three_and_deduped() -> None:
    plan = plan_turn("consent notice rights breach children", _state())
    citations = build_citations(plan)
    assert len(citations) <= 3
    assert len({c.url for c in citations}) == len(citations)


def test_actions_are_capped_at_three_and_deduped() -> None:
    plan = plan_turn("do I need consent for marketing in my ca firm?", _state())
    actions = build_actions(plan)
    assert len(actions) <= 3
    assert len({a.url for a in actions}) == len(actions)
    assert all(a.type == "open_url" for a in actions)


def test_followups_track_the_detected_journey() -> None:
    plan = plan_turn("do I need consent for this?", _state())
    assert plan.journey == "J4"
    assert build_followups(plan) == [
        "What must my privacy notice say?",
        "What makes consent valid?",
    ]


def test_grounding_block_wraps_every_chunk_with_its_source() -> None:
    plan = plan_turn("what must my privacy notice say?", _state())
    block = build_grounding_block(plan)
    assert block.startswith("<retrieved_context>")
    assert "</retrieved_context>" in block
    assert "https://saralprivacy.com/" in block


def test_grounding_block_names_the_journey_and_industry_when_known() -> None:
    plan = plan_turn("do I need consent for my ca firm?", _state())
    block = build_grounding_block(plan)
    assert "<journey>J4" in block
    assert "<industry>ca-firms</industry>" in block


def test_guarded_meta_leaks_nothing_and_offers_no_leads() -> None:
    meta = guarded_meta()
    assert meta.citations == []
    assert [a.url for a in meta.actions] == ["/faq", "/contact"]
    assert meta.refusal is True
    assert meta.confidence == "low"
    assert meta.escalation_reason is None
    assert meta.to_dict("abc")["sig"] == "abc"


def test_meta_serialises_to_the_widget_contract() -> None:
    plan = plan_turn("do I need consent for marketing?", _state())
    payload = build_meta(plan).to_dict("deadbeef")
    for key in (
        "citations",
        "actions",
        "confidence",
        "refusal",
        "piiWarning",
        "suggestedFollowups",
        "animation",
        "disclaimer",
        "sig",
    ):
        assert key in payload, key
    assert payload["animation"] == {"state": "pointing"}
    assert payload["disclaimer"] == "Educational only — not legal advice."


@pytest.mark.parametrize("absent", ["journey", "industry", "escalation"])
def test_absent_optional_fields_are_omitted_not_null(absent: str) -> None:
    plan = plan_turn("what is dpdpa", _state())
    payload = build_meta(plan).to_dict()
    if absent in payload:
        assert payload[absent] is not None
