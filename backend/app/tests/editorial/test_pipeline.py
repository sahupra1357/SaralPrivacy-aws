"""Daily briefing pipeline steps and the job (roadmap → research → content → image →
publish), every external mocked: Google Sheets, SerpAPI, Anthropic, KIE.ai, storage."""

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlmodel import Session, col, select

from app.editorial import config
from app.editorial.pipeline import content as content_step
from app.editorial.pipeline import image as image_step
from app.editorial.pipeline import publish as publish_step
from app.editorial.pipeline import research as research_step
from app.editorial.pipeline import roadmap
from app.jobs import editorial as job
from app.models.editorial import Briefing

TARGET = date(2026, 5, 30)

ROW: dict[str, Any] = {
    "day": "91",
    "week": "14",
    "week_theme": "Industry data reality",
    "topic": "CA firms: what client data you hold",
    "concept": "Know your data",
    "clarification": "PAN and Aadhaar are personal data",
    "save_worthy_takeaway": "You cannot protect what you have not listed.",
    "publish_channels": "email|web",
    "infographic_type": "stat",
    "research_query": "CA firm client data DPDPA",
    "Plan Published Date": "30-May-26",
    "Published": "",
}


def content_json(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "topic": ROW["topic"],
        "date": "2026-05-30",
        "day_number": 91,
        "subject_line": "Do you know what client data you hold?",
        "preview_text": "Most CA firms do not.",
        "overview": {"heading": "Hook", "body": "Your files hold PAN numbers."},
        "key_points": {"heading": "3 things", "points": ["one", "two"]},
        "what_this_means": {
            "heading": "What does this mean for YOUR business?",
            "body": "List it.",
        },
        "action_items": {
            "heading": "3 things you can do this week",
            "items": [
                {"priority": "high", "action": "List client data", "owner": "Founder"},
                {"priority": "low", "action": "", "owner": "Founder"},
            ],
        },
        "save_worthy_takeaway": ROW["save_worthy_takeaway"],
        "infographic": {
            "type": "stat",
            "title": "Know your data",
            "description": "d",
            "data_points": ["a"],
        },
        "sources_used": [],
        "word_count": 0,
    }
    data.update(overrides)
    return data


@pytest.fixture
def csv_roadmap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "roadmap.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(ROW))
        writer.writeheader()
        writer.writerow(ROW)
        writer.writerow({**ROW, "day": "92", "Plan Published Date": "31-May-26"})
    monkeypatch.setattr(config, "roadmap_csv_path", lambda: str(path))
    monkeypatch.setattr(config, "google_sheet_id", lambda: "")
    return path


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    for mod in (research_step, content_step, image_step):
        monkeypatch.setattr(mod, "_sleep", lambda s: None)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any stray httpx call fails loudly; tests that need one patch it explicitly."""
    calls: list[str] = []

    def refuse(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
        calls.append(str(args[0]) if args else "")
        raise httpx.ConnectError("network disabled in tests")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx, "post", refuse)
    return calls


# ── roadmap ────────────────────────────────────────────────────────────────
def test_sheet_dates_parse_and_format() -> None:
    assert roadmap.parse_sheet_date("1-Mar-26") == date(2026, 3, 1)
    assert roadmap.parse_sheet_date("garbage") is None
    assert roadmap.sheet_date_str(date(2026, 3, 1)) == "1-Mar-26"


def test_pick_returns_the_planned_row_with_facets() -> None:
    result = roadmap.pick([ROW], TARGET)
    assert result["skip"] is False
    assert result["day"] == 91 and result["topic"] == ROW["topic"]
    assert result["publish_channels"] == ["email", "web"]
    assert (result["stage"], result["sector"], result["content_type"]) == (
        "assess",
        "ca-firms",
        "stat",
    )


def test_pick_skips_published_rows_and_other_dates() -> None:
    assert roadmap.pick([{**ROW, "Published": "Yes"}], TARGET)["skip"] is True
    skipped = roadmap.pick([ROW], date(2026, 6, 1))
    assert skipped == {"skip": True, "reason": "no_planned_topic_today", "date": "2026-06-01"}


def test_csv_fallback_and_mark_published(csv_roadmap: Path) -> None:
    assert roadmap.read_roadmap(TARGET)["day"] == 91
    assert roadmap.mark_published(91, date(2026, 5, 30)) is True
    rows = list(csv.DictReader(open(csv_roadmap, encoding="utf-8")))
    assert rows[0]["Published"] == "Yes" and rows[0]["sent"] == "done"
    assert rows[0]["Actual Publish Date"] == "30-May-26"
    assert roadmap.read_roadmap(TARGET)["skip"] is True


def test_missing_roadmap_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "roadmap_csv_path", lambda: "/nonexistent.csv")
    monkeypatch.setattr(config, "google_sheet_id", lambda: "")
    with pytest.raises(roadmap.RoadmapUnavailable):
        roadmap.load_rows()


# ── research ───────────────────────────────────────────────────────────────
def test_research_without_a_key_is_knowledge_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "serp_api_key", lambda: "")
    out = research_step.research({"topic": "t"})
    assert out["knowledge_only"] is True and out["sources"] == []


def test_research_runs_two_queries_dedupes_and_ranks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "serp_api_key", lambda: "serp")
    queries: list[str] = []

    def fake_search(query: str, api_key: str) -> list[dict[str, Any]]:  # noqa: ARG001
        queries.append(query)
        return [
            {"title": "A", "link": "https://a", "snippet": "ca firms client data"},
            {"title": "B", "link": "https://b", "snippet": "unrelated"},
            {"title": "Empty", "link": "https://c", "snippet": ""},
        ]

    monkeypatch.setattr(research_step, "search_serp", fake_search)
    out = research_step.research({"topic": "CA firms client data", "research_query": "q1"})
    assert queries == ["q1", "DPDPA India CA firms client data SMB business compliance"]
    assert [s["url"] for s in out["sources"]] == ["https://a", "https://b"]
    assert out["raw_snippets"].startswith("[A] (https://a)")
    assert out["query_count"] == 2


# ── content ────────────────────────────────────────────────────────────────
def test_generate_content_validates_and_counts_words(mock_llm: Any) -> None:
    mock_llm.responses["complete"] = "```json\n" + json.dumps(content_json()) + "\n```"
    out = content_step.generate_content(
        {"topic_data": {"topic": ROW["topic"], "day": 91}, "raw_snippets": ""}
    )
    assert out["word_count"] > 0
    call = mock_llm.last()
    assert call["system"].startswith("You are the writer of DPDPA Daily Brief")
    assert (
        call["model"] == "claude-sonnet-4-6"
        and call["max_tokens"] == 4096
        and call["temperature"] == 0.3
    )
    assert "## Note: No web research available." in call["messages"][0]["content"]


def test_generate_content_retries_then_fails(mock_llm: Any) -> None:
    mock_llm.responses["complete"] = "{not json"
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        content_step.generate_content({"topic_data": {}})
    assert len(mock_llm.calls) == 3


# ── image ──────────────────────────────────────────────────────────────────
def test_image_skipped_without_a_kie_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "kie_api_key", lambda: "")
    assert image_step.generate_image(content_json()) is None


def test_image_prompt_keeps_the_brand_instructions() -> None:
    prompt = image_step.build_prompt(content_json())
    assert 'Title: "Know your data"' in prompt
    assert "DPDPA Daily Brief — Day 91" in prompt
    assert "- a" in prompt


def test_image_returns_watermarked_bytes_or_none_after_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "kie_api_key", lambda: "kie")
    monkeypatch.setattr(image_step, "call_kie", lambda prompt, key: b"raw")
    monkeypatch.setattr(image_step, "add_watermark", lambda b: b + b"-wm")
    assert image_step.generate_image(content_json()) == b"raw-wm"

    attempts: list[int] = []

    def fail(prompt: str, key: str) -> bytes:  # noqa: ARG001
        attempts.append(1)
        raise image_step.KieError("down")

    monkeypatch.setattr(image_step, "call_kie", fail)
    assert image_step.generate_image(content_json()) is None
    assert len(attempts) == 3


def test_watermark_returns_original_bytes_for_non_images() -> None:
    assert image_step.add_watermark(b"not an image") == b"not an image"


# ── publish ────────────────────────────────────────────────────────────────
def test_build_payload_packs_facets_and_checklist() -> None:
    payload = publish_step.build_payload(
        {**content_json(), "week_theme": "Industry data reality"}, "https://img"
    )
    why = json.loads(payload["why_it_matters"])
    assert why["save"] == ROW["save_worthy_takeaway"] and why["stage"] == "assess"
    assert payload["category"] == "assess" and payload["industries"] == ["ca-firms"]
    assert payload["tags"][0] == "explainer"  # no infographic_type on the content → explainer
    assert payload["action_checklist"] == ["List client data"]
    assert payload["read_time"] == 3
    assert payload["infographic_url"] == "https://img"


def test_publish_uploads_inserts_and_revalidates(
    session: Session, mock_storage: Any, mock_revalidate: Any
) -> None:
    briefing = publish_step.publish(session, content_json(), b"jpeg")
    assert mock_storage.last()["key"] == "infographics/inf20260530.jpg"
    assert briefing.status == "approved"
    assert briefing.slug == "2026-05-30-do-you-know-what-client-data-you-hold"
    assert briefing.infographic_base64 and briefing.infographic_base64.endswith("inf20260530.jpg")
    assert {"tag": "briefings"} in mock_revalidate.calls


# ── job ────────────────────────────────────────────────────────────────────
def _stub_steps(monkeypatch: pytest.MonkeyPatch, mock_llm: Any) -> None:
    monkeypatch.setattr(config, "serp_api_key", lambda: "")
    monkeypatch.setattr(config, "kie_api_key", lambda: "")
    mock_llm.responses["complete"] = json.dumps(content_json())


def test_job_publishes_marks_the_roadmap_and_is_idempotent(
    session: Session,
    csv_roadmap: Path,  # noqa: ARG001 — fixture used for its side effect (writes the CSV)
    mock_llm: Any,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    _stub_steps(monkeypatch, mock_llm)
    first = job.run_pipeline(session, TARGET)
    assert first.ok and first.details and first.details["slug"].startswith("2026-05-30-")
    assert first.details["has_infographic"] is False

    second = job.run_pipeline(session, TARGET)
    assert second.ok and "skipped" in second.summary
    rows = session.exec(select(Briefing).where(col(Briefing.slug).startswith("2026-05-30-"))).all()
    assert len(rows) == 1


def test_job_db_guard_skips_even_if_the_sheet_was_not_marked(
    session: Session,
    csv_roadmap: Path,  # noqa: ARG001 — fixture used for its side effect (writes the CSV)
    mock_llm: Any,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ARG001
) -> None:
    _stub_steps(monkeypatch, mock_llm)
    session.add(Briefing(title="Already", slug="2026-05-30-already", status="approved"))
    session.commit()
    result = job.run_pipeline(session, TARGET)
    assert result.ok and "already published" in result.summary
    assert mock_llm.calls == []


def test_job_skips_cleanly_when_nothing_is_planned(session: Session, csv_roadmap: Path) -> None:  # noqa: ARG001
    result = job.run_pipeline(session, date(2027, 1, 1))
    assert result.ok and "No topic planned" in result.summary


def test_job_fails_when_the_roadmap_is_unavailable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "roadmap_csv_path", lambda: "")
    monkeypatch.setattr(config, "google_sheet_id", lambda: "")
    assert job.run_pipeline(session, TARGET).ok is False


def test_backfill_taxonomy_dry_run_then_apply_then_revert(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mock_revalidate: Any,  # noqa: ARG001
) -> None:
    session.add(
        Briefing(
            title="T",
            slug="day-91-slug",
            status="approved",
            category="compliance-guidance",
            tags='["ca"]',
            industries='["general"]',
        )
    )
    session.add(Briefing(title="U", slug="no-roadmap-row", status="approved"))
    session.commit()
    monkeypatch.setattr(
        roadmap,
        "load_rows",
        lambda: [
            {
                "slug": "day-91-slug",
                "week_theme": "Act now",
                "topic": "NBFCs: x",
                "infographic_type": "checklist",
            }
        ],
    )

    dry = job.backfill_taxonomy(session, apply=False)
    assert dry["changes"] >= 1 and dry["applied"] == 0
    row = session.exec(select(Briefing).where(Briefing.slug == "day-91-slug")).one()
    assert row.category == "compliance-guidance"

    applied = job.backfill_taxonomy(session, apply=True, backup_dir=tmp_path)
    session.refresh(row)
    assert applied["applied"] >= 1
    assert (row.category, row.industries, row.tags) == ("fix", '["nbfc"]', '["checklist","ca"]')
    assert job.backfill_taxonomy(session, apply=False)["changes"] == 0  # re-run is a no-op

    restored = job.revert_taxonomy(session, Path(applied["backup"]))
    session.refresh(row)
    assert restored >= 1 and row.category == "compliance-guidance"
