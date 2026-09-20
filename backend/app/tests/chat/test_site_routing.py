"""The allowlist that makes an invented URL impossible. Ported from
frontend/lib/chat/site-routing.test.ts (the dead-link half stays in the frontend suite,
where the Next.js app/ directory can be walked).
"""

import pytest

from app.services.chat import site_routing


def test_the_exported_table_is_complete() -> None:
    assert len(site_routing.routes()) > 40
    assert len(site_routing.industry_slugs()) == 12
    assert all(r.url.startswith("/") for r in site_routing.routes())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/learn/consent", "/learn/consent"),
        ("https://saralprivacy.com/learn/consent", "/learn/consent"),
        ("/learn/consent/", "/learn/consent"),
        ("https://saralprivacy.com", "/"),
    ],
)
def test_normalize_path_canonicalises(raw: str, expected: str) -> None:
    assert site_routing.normalize_path(raw) == expected


@pytest.mark.parametrize(
    "raw", ["https://evil.example/learn", "javascript:alert(1)", "/learn?x=1", "/learn#top"]
)
def test_normalize_path_rejects_anything_non_canonical(raw: str) -> None:
    assert site_routing.normalize_path(raw) is None


def test_valid_citations_are_allowlisted_paths_only() -> None:
    assert site_routing.is_valid_citation("/learn/consent") is True
    assert site_routing.is_valid_citation("/contact") is True
    assert site_routing.is_valid_citation("https://saralprivacy.com/faq") is True
    assert site_routing.is_valid_citation("/learn/made-up-page") is False
    assert site_routing.is_valid_citation("https://evil.example/learn/consent") is False


def test_never_surfaced_prefixes_are_refused_even_if_they_look_like_pages() -> None:
    assert site_routing.is_never_surfaced("/admin/leads") is True
    assert site_routing.is_never_surfaced("/api/chat") is True
    assert site_routing.is_never_surfaced("/assessment/step-1") is True
    # The bare hub stays linkable — only the quiz steps are banned.
    assert site_routing.is_never_surfaced("/assessment") is False
    assert site_routing.is_valid_citation("/assessment") is True
    assert site_routing.is_valid_citation("/admin/leads") is False


def test_authority_citations_are_tier_1_or_2_only() -> None:
    assert site_routing.is_authority_citation("/learn/consent") is True
    assert site_routing.is_authority_citation("/white-paper") is False  # tier 3
    assert site_routing.is_authority_citation("/briefings") is False  # tier 4
    assert site_routing.is_authority_citation("/contact") is False  # utility


def test_utility_pages_are_linkable_but_never_authority() -> None:
    for url in site_routing.exclude_from_authority():
        assert site_routing.is_valid_citation(url) is True
        assert site_routing.is_authority_citation(url) is False


def test_routes_for_topic_ranks_tier_1_first() -> None:
    consent = site_routing.routes_for_topic("consent")
    assert consent
    assert consent[0].tier == 1
    assert site_routing.routes_for_topic("") == []


def test_every_industry_has_a_guide() -> None:
    for slug in site_routing.industry_slugs():
        route = site_routing.route_for_industry(slug)
        assert route is not None, slug
        assert site_routing.is_valid_citation(route.url)


@pytest.mark.parametrize("intent", ["assessment", "penalty", "discovery", "notice", "whitepaper"])
def test_tool_for_intent_covers_every_intent(intent: str) -> None:
    route = site_routing.tool_for_intent(intent)
    assert route is not None
    assert site_routing.is_valid_citation(route.url)


def test_tool_for_intent_rejects_an_unknown_intent() -> None:
    assert site_routing.tool_for_intent("nonsense") is None
