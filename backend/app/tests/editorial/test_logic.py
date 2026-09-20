"""Pure editorial logic: text helpers, taxonomy, briefing builders, infographic SVG,
validator parsing and the approval email."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from app.editorial import blog_ai, emails, infographic
from app.editorial import briefings as logic
from app.editorial.docs import (
    briefing_doc,
    briefing_slug,
    first_truthy,
    js_json,
    js_round,
    slugify_title,
)
from app.editorial.taxonomy import content_type_for, derive, sector_for, stage_for
from app.models.editorial import Briefing


# ── docs / text helpers ────────────────────────────────────────────────────
def test_slugify_matches_the_js_rule() -> None:
    assert (
        slugify_title("  Can You Keep Ex-Employee Records Forever?  ")
        == "can-you-keep-ex-employee-records-forever"
    )
    assert len(slugify_title("x" * 100)) == 60
    assert briefing_slug("2026-05-30", "Hello, World!") == "2026-05-30-hello-world"


def test_js_round_rounds_halves_up() -> None:
    assert js_round(0.5) == 1
    assert js_round(2.5) == 3
    assert js_round(0.49) == 0


def test_first_truthy_and_js_json() -> None:
    assert first_truthy("", None, "b") == "b"
    assert first_truthy("", 0) == ""
    assert js_json({"a": "₹"}) == '{"a":"₹"}'


def test_briefing_doc_maps_created_at_attr_and_hides_the_token() -> None:
    row = Briefing(
        title="T",
        slug="s",
        approval_token="secret",
        created_at_attr=datetime(2026, 1, 2, tzinfo=UTC),
    )
    doc = briefing_doc(row)
    assert doc["created_at"] == "2026-01-02T00:00:00+00:00"
    assert "approval_token" not in doc and "created_at_attr" not in doc
    assert briefing_doc(row, include_private=True)["approval_token"] == "secret"


# ── taxonomy ───────────────────────────────────────────────────────────────
def test_taxonomy_prefers_explicit_columns_then_derives() -> None:
    assert stage_for("Industry response packs") == "sustain"
    assert stage_for("anything", explicit="FIX") == "fix"
    assert stage_for("unknown") == "learn"
    assert sector_for("CA firms: client KYC") == "ca-firms"
    assert sector_for("No prefix here") == "general"
    assert content_type_for("process", "sustain") == "playbook"
    assert content_type_for("comparison", "learn") == "explainer"
    assert derive(
        {"week_theme": "Act now", "topic": "NBFCs: loans", "infographic_type": "checklist"}
    ) == {
        "stage": "fix",
        "sector": "nbfc",
        "content_type": "checklist",
    }


# ── briefing builders ──────────────────────────────────────────────────────
def test_topic_and_theme_follow_day_of_year_and_weekday() -> None:
    topic, theme = logic.pick_topic_and_theme(datetime(2026, 1, 1, 12, tzinfo=UTC))  # Thursday
    assert topic == logic.DPDPA_TOPICS[0]
    assert theme["type"] == "audit"
    _, sunday = logic.pick_topic_and_theme(datetime(2026, 1, 4, 12, tzinfo=UTC))
    assert sunday["type"] == "recap"


@pytest.mark.parametrize(
    ("theme", "expected"),
    [
        ("audit", "audit"),
        ("case", "team"),
        ("recap", "team"),
        ("mistake", "mistake"),
        ("example", "mistake"),
        ("myth", "today"),
    ],
)
def test_action_format(theme: str, expected: str) -> None:
    assert logic.action_format_for(theme) == expected


def test_parse_claude_json_strips_fences() -> None:
    assert logic.parse_claude_json('```json\n{"title": "x"}\n```') == {"title": "x"}
    with pytest.raises(ValueError):
        logic.parse_claude_json("[1, 2]")


def test_manual_read_time_counts_words_over_200() -> None:
    assert logic.manual_read_time({"read_time": 7}) == 7
    assert logic.manual_read_time({"title": "word " * 300}) == 2  # round(1.5) = 2
    assert logic.manual_read_time({"title": "short"}) == 1


def test_tomorrow_0330_utc() -> None:
    assert logic.tomorrow_0330_utc(datetime(2026, 9, 18, 22, 0, tzinfo=UTC)) == datetime(
        2026, 9, 19, 3, 30, tzinfo=UTC
    )


def test_today_ist_window_spans_the_ist_day() -> None:
    today, start, end = logic.today_ist_window(
        datetime(2026, 9, 18, 20, 0, tzinfo=UTC)
    )  # 01:30 IST on the 19th
    assert today == "2026-09-19"
    assert start.astimezone(UTC) == datetime(2026, 9, 18, 18, 30, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026, 9, 19, 18, 29, 59, tzinfo=UTC)


def test_build_auto_briefing_packs_the_v2_envelope() -> None:
    now = datetime(2026, 9, 18, 3, 0, tzinfo=UTC)
    topic, theme = logic.DPDPA_TOPICS[0], logic.DOW_THEMES[1]
    row = logic.build_auto_briefing(
        {"title": "Q?", "save_line": "S", "action_items": ["a"]}, topic, theme, now, now
    )
    why = json.loads(row.why_it_matters or "")
    assert (
        why["version"] == 2 and why["theme_label"] == "Big Idea" and why["action_format"] == "today"
    )
    assert row.category == topic["category"]
    assert json.loads(row.industries or "") == ["general"]
    assert row.read_time == 1 and row.status == "approved"


# ── infographic ────────────────────────────────────────────────────────────
def test_wrap_text_adds_an_ellipsis_when_truncated() -> None:
    lines = infographic.wrap_text("one two three four five six", max_chars=9, max_lines=2)
    assert lines == ["one two", "three…"]
    assert infographic.wrap_text("", 10, 2) == []


def test_split_bullets_strips_markers() -> None:
    assert infographic.split_bullets("- first\n2) second\n\n• third", 2) == ["first", "second"]


@pytest.mark.parametrize(
    ("lane", "marker"),
    [
        ("law-explained", "WHAT CHANGED"),
        ("compliance-playbook", 'r="28"'),
        ("myth-fact", "VERDICT"),
        ("sector-notes", "stroke-linecap"),
        ("governance-watch", "#E8AB42"),
        ("unknown-lane", "WHAT CHANGED"),
    ],
)
def test_build_svg_picks_the_layout_by_lane(lane: str, marker: str) -> None:
    svg = infographic.build_svg(
        infographic.InfographicInput(
            title="A & B <title>", lane=lane, section_what_changed="x\ny", section_do_now="z"
        )
    )
    assert marker in svg
    assert "A &amp; B &lt;title&gt;" in svg
    assert "Verified DPDPA insights  ·  saralprivacy.com" in svg


# ── blog validator parsing ─────────────────────────────────────────────────
def test_parse_validation_accepts_fenced_json_and_rejects_bad_enums() -> None:
    good = {
        "scores": dict.fromkeys(
            (
                "score_legal_accuracy",
                "score_primary_source",
                "score_currency",
                "score_scope",
                "score_operational",
                "total",
            ),
            1,
        ),
        "section_feedback": [{"section": "do_now", "status": "verified", "note": "ok"}],
        "suggested_sources": [],
        "editorial_notes": "",
    }
    assert blog_ai.parse_validation("```json\n" + json.dumps(good) + "\n```") is not None
    bad = {**good, "section_feedback": [{"section": "nope", "status": "verified", "note": ""}]}
    assert blog_ai.parse_validation(json.dumps(bad)) is None


def test_ist_date_shifts_by_five_thirty() -> None:
    assert blog_ai.ist_date(datetime(2026, 9, 18, 19, 0, tzinfo=UTC)) == "2026-09-19"


# ── approval email ─────────────────────────────────────────────────────────
def test_approval_email_goes_to_every_admin_with_the_approve_link(
    mock_email: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "EXTRA_ADMIN_EMAILS", "extra@saral.test")
    row = Briefing(title="T", slug="s", action_checklist='["one"]')
    assert emails.send_approval_email(row, "tok") is True
    recipients = [c["to"][0] for c in mock_email.calls]
    assert recipients == [*emails.DEFAULT_ADMIN_EMAILS, "extra@saral.test"]
    call = mock_email.last()
    assert call["subject"] == "New Briefing LIVE: T — Send to Subscribers"
    assert f"/api/briefings/approve?token=tok&amp;briefingId={row.id}" in call["html"]
    assert "Immediately" in call["html"]
