"""Plane 2 typed lookups. Ported from frontend/lib/chat/knowledge-tools.test.ts."""

from app.services.chat.knowledge import lookup_checklist, lookup_glossary


def test_exact_term_lookup_returns_the_definition_and_its_neighbours() -> None:
    hit = lookup_glossary("Data Fiduciary")
    assert hit.best is not None
    assert hit.best.term == "Data Fiduciary"
    assert hit.best.section.startswith("Section")
    assert hit.best.url == "/glossary"
    assert hit.related


def test_lookup_by_id_works_too() -> None:
    hit = lookup_glossary("data-principal")
    assert hit.best is not None
    assert hit.best.term == "Data Principal"


def test_near_match_still_finds_the_term() -> None:
    hit = lookup_glossary("significant data fiduciary")
    assert hit.best is not None
    assert "Fiduciary" in hit.best.term


def test_unrelated_query_matches_nothing() -> None:
    assert lookup_glossary("sourdough starter hydration").best is None


def test_empty_query_matches_nothing() -> None:
    result = lookup_glossary("   ")
    assert result.best is None
    assert result.related == []


def test_checklist_lookup_by_exact_id() -> None:
    hits = lookup_checklist("1.1")
    assert len(hits) == 1
    assert hits[0].id == "1.1"
    assert hits[0].url == "/compliance-checklist"


def test_checklist_lookup_by_topic_keywords() -> None:
    hits = lookup_checklist("retention and deletion of old records")
    assert hits
    assert len(hits) <= 5


def test_checklist_lookup_returns_nothing_for_nonsense() -> None:
    assert lookup_checklist("zzz qqq xxx") == []


def test_checklist_lookup_returns_nothing_for_an_unknown_id() -> None:
    assert lookup_checklist("99.99") == []
