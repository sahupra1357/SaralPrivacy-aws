"""Routes ported from `app/api/blog/{save,validate,revise,infographic,[id]}/route.ts`
plus the public/admin read endpoints."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.editorial import infographic
from app.models.editorial import BlogPost

BASE = "/api/v1/blog"

SAVE_PAYLOAD: dict[str, Any] = {
    "title": "What the DPDP Rules 2025 change",
    "slug": "/dpdp-rules-2025-change/ ",
    "excerpt": "Plain-language summary.",
    "lane": "law-explained",
    "author": "Desk",
    "tags": "rules,consent",
    "featured": False,
    "status": "draft",
    "section_what_changed": "The Rules were notified.",
    "section_law_says": "Section 6 requires consent.",
    "section_do_now": "Blank",
    "section_uncertain": "  ",
    "section_mistakes": "Keeping CVs forever.",
    "primary_sources": [
        {"claim": "c", "sourceType": "Act text", "citation": "s6", "riskLevel": "Low"}
    ],
    "validated_at": "2026-09-18",
    "score_legal_accuracy": 30,
    "score_primary_source": 20,
    "score_currency": 15,
    "score_scope": 10,
    "score_operational": 10,
    "scope_labels": {"x": "y"},
}


# ── auth ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/save"),
        ("patch", "/save"),
        ("post", "/validate"),
        ("post", "/revise"),
        ("post", "/infographic"),
        ("get", "/some-id"),
        ("get", "/admin/all"),
    ],
)
def test_editor_routes_require_a_session(client: TestClient, method: str, path: str) -> None:
    res = client.request(method.upper(), f"{BASE}{path}", json={})
    assert res.status_code == 401


# ── POST /blog/save ─────────────────────────────────────────────────────────
def test_save_creates_a_post_and_busts_the_blog_cache(
    client: TestClient, session: Session, blogger_headers: dict[str, str], mock_revalidate: Any
) -> None:
    res = client.post(f"{BASE}/save", json=SAVE_PAYLOAD, headers=blogger_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["slug"] == "dpdp-rules-2025-change"

    row = session.exec(select(BlogPost).where(BlogPost.slug == "dpdp-rules-2025-change")).one()
    assert str(row.id) == body["id"]
    assert row.validation_score == 85
    assert row.read_time == 1
    assert row.published_at is None
    sections = json.loads(row.sections_json or "")
    assert sections["section_do_now"] is None  # "Blank" placeholder is dropped
    assert sections["section_uncertain"] is None
    assert sections["section_mistakes"] == "Keeping CVs forever."
    assert json.loads(sections["primary_sources"])[0]["citation"] == "s6"

    assert {"path": "/blog/dpdp-rules-2025-change"} in mock_revalidate.calls
    assert {"path": "/blog"} in mock_revalidate.calls
    assert {"tag": "blog-posts"} in mock_revalidate.calls


def test_save_publish_stamps_published_at(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        f"{BASE}/save", json={**SAVE_PAYLOAD, "status": "published"}, headers=admin_headers
    )
    row = session.exec(select(BlogPost).where(BlogPost.slug == res.json()["slug"])).one()
    assert row.published_at and len(row.published_at) == 10


def test_save_requires_title_and_slug(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(f"{BASE}/save", json={**SAVE_PAYLOAD, "title": ""}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "Title and slug are required"}


def test_save_rejects_a_slash_only_slug(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(f"{BASE}/save", json={**SAVE_PAYLOAD, "slug": " // "}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "Slug cannot be only slashes or whitespace"}


# ── PATCH /blog/save ────────────────────────────────────────────────────────
def test_patch_requires_an_id(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.patch(f"{BASE}/save", json=SAVE_PAYLOAD, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "Document ID required for update"}


def test_patch_rejects_a_slash_only_slug(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.patch(
        f"{BASE}/save", json={**SAVE_PAYLOAD, "id": "x", "slug": "/"}, headers=admin_headers
    )
    assert res.status_code == 400


def test_patch_updates_the_post(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    make_post: Callable[..., BlogPost],
) -> None:
    post = make_post(status="draft")
    res = client.patch(
        f"{BASE}/save",
        json={**SAVE_PAYLOAD, "id": str(post.id), "title": "Renamed", "status": "review"},
        headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json() == {"success": True, "id": str(post.id), "slug": "dpdp-rules-2025-change"}
    session.refresh(post)
    assert post.title == "Renamed" and post.status == "review"


# ── GET /blog/{id} ──────────────────────────────────────────────────────────
def test_get_post_returns_the_document(
    client: TestClient, blogger_headers: dict[str, str], make_post: Callable[..., BlogPost]
) -> None:
    post = make_post(status="draft", legacy_id="appwrite-123")
    by_uuid = client.get(f"{BASE}/{post.id}", headers=blogger_headers)
    assert by_uuid.status_code == 200
    assert by_uuid.json()["$id"] == str(post.id)
    assert client.get(f"{BASE}/appwrite-123", headers=blogger_headers).json()["slug"] == post.slug


def test_get_post_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.get(f"{BASE}/00000000-0000-0000-0000-000000000000", headers=admin_headers)
    assert res.status_code == 404
    assert res.json() == {"error": "Not found"}


# ── POST /blog/validate ─────────────────────────────────────────────────────
VALID_OUTPUT = {
    "scores": {
        "score_legal_accuracy": 30,
        "score_primary_source": 20,
        "score_currency": 12,
        "score_scope": 13,
        "score_operational": 8,
        "total": 83,
    },
    "section_feedback": [{"section": "law_says", "status": "warning", "note": "Cite the Rule."}],
    "suggested_sources": [
        {"claim": "c", "sourceType": "Notified Rules", "citation": "Rule 3", "riskLevel": "Medium"}
    ],
    "editorial_notes": "Mostly fine.",
}


def test_validate_requires_title_and_first_section(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(f"{BASE}/validate", json={"title": "T"}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "Title and at least one content section required"}


def test_validate_returns_the_report_with_an_ist_date(
    client: TestClient, admin_headers: dict[str, str], mock_llm: Any
) -> None:
    mock_llm.responses["complete"] = "```json\n" + json.dumps(VALID_OUTPUT) + "\n```"
    res = client.post(
        f"{BASE}/validate",
        json={"title": "T", "lane": "law-explained", "section_what_changed": "Rules notified."},
        headers=admin_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["scores"]["total"] == 83
    assert body["section_feedback"][0]["note"] == "Cite the Rule."
    assert len(body["validated_at"]) == 10
    call = mock_llm.last()
    assert call["model"] == "claude-sonnet-4-6"
    assert (
        "--- SECTION: What Businesses Should Do Now ---\n(empty)" in call["messages"][0]["content"]
    )


def test_validate_502_when_the_model_breaks_the_schema(
    client: TestClient, admin_headers: dict[str, str], mock_llm: Any
) -> None:
    mock_llm.responses["complete"] = '{"scores": {}}'
    res = client.post(
        f"{BASE}/validate", json={"title": "T", "section_what_changed": "x"}, headers=admin_headers
    )
    assert res.status_code == 502
    assert res.json() == {
        "error": "Validation model did not return a structured response. Please try again."
    }


# ── POST /blog/revise ───────────────────────────────────────────────────────
def test_revise_requires_its_three_fields(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        f"{BASE}/revise", json={"sectionKey": "section_law_says"}, headers=admin_headers
    )
    assert res.status_code == 400
    assert res.json() == {"error": "sectionKey, currentContent, and feedbackNote are required"}


def test_revise_returns_trimmed_content_with_other_sections_as_context(
    client: TestClient, blogger_headers: dict[str, str], mock_llm: Any
) -> None:
    mock_llm.responses["complete"] = "  Corrected text.  "
    res = client.post(
        f"{BASE}/revise",
        json={
            "sectionKey": "section_law_says",
            "currentContent": "Old",
            "feedbackNote": "Cite the section.",
            "title": "T",
            "section_what_changed": "Changed",
            "section_law_says": "Old",
        },
        headers=blogger_headers,
    )
    assert res.status_code == 200
    assert res.json() == {"revisedContent": "Corrected text."}
    prompt = mock_llm.last()["messages"][0]["content"]
    assert 'Section being corrected: "What the Law Actually Says"' in prompt
    assert "--- What Changed ---\nChanged" in prompt
    assert "--- What the Law Actually Says ---" not in prompt


def test_revise_502_on_empty_model_output(
    client: TestClient, admin_headers: dict[str, str], mock_llm: Any
) -> None:
    mock_llm.responses["complete"] = "   "
    res = client.post(
        f"{BASE}/revise",
        json={"sectionKey": "section_do_now", "currentContent": "x", "feedbackNote": "y"},
        headers=admin_headers,
    )
    assert res.status_code == 502
    assert res.json() == {"error": "Revision model returned empty content. Please try again."}


# ── POST /blog/infographic ──────────────────────────────────────────────────
def test_infographic_requires_id_and_title(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(f"{BASE}/infographic", json={"id": "x"}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "id and title are required"}


def test_infographic_renders_uploads_and_stores_the_url(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    make_post: Callable[..., BlogPost],
    mock_storage: Any,
    mock_revalidate: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered: list[str] = []
    monkeypatch.setattr(
        infographic, "rasterize_png", lambda svg: rendered.append(svg) or b"\x89PNG"
    )
    post = make_post()
    res = client.post(
        f"{BASE}/infographic",
        json={
            "id": str(post.id),
            "title": "Timeline",
            "lane": "governance-watch",
            "section_what_changed": "a\nb",
        },
        headers=admin_headers,
    )
    assert res.status_code == 200
    url = res.json()["url"]
    assert f"infographics/blog_inf_{post.id}.png?v=" in url
    assert mock_storage.last()["content_type"] == "image/png"
    assert 'fill="#E8AB42"' in rendered[0]  # timeline layout's gold milestones
    session.refresh(post)
    assert post.infographic_url == url
    assert {"path": f"/blog/{post.slug}"} in mock_revalidate.calls


def test_infographic_500_when_rendering_fails(
    client: TestClient,
    admin_headers: dict[str, str],
    make_post: Callable[..., BlogPost],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(svg: str) -> bytes:  # noqa: ARG001
        raise RuntimeError("chromium missing")

    monkeypatch.setattr(infographic, "rasterize_png", boom)
    post = make_post()
    res = client.post(
        f"{BASE}/infographic", json={"id": str(post.id), "title": "T"}, headers=admin_headers
    )
    assert res.status_code == 500
    assert res.json() == {"error": "chromium missing"}


# ── public + admin reads ────────────────────────────────────────────────────
def test_public_list_filters_published_lane_and_excluded_slug(
    client: TestClient, make_post: Callable[..., BlogPost]
) -> None:
    make_post(slug="a", lane="myth-fact")
    make_post(slug="b", lane="myth-fact")
    make_post(slug="c", lane="law-explained")
    make_post(slug="d", lane="myth-fact", status="draft")
    res = client.get(f"{BASE}?lane=myth-fact&exclude_slug=a")
    assert res.status_code == 200
    assert [d["slug"] for d in res.json()["docs"]] == ["b"]


def test_public_by_slug_only_serves_published(
    client: TestClient, make_post: Callable[..., BlogPost]
) -> None:
    make_post(slug="live")
    make_post(slug="hidden", status="draft")
    assert client.get(f"{BASE}/by-slug/live").status_code == 200
    assert client.get(f"{BASE}/by-slug/hidden").status_code == 404


def test_admin_list_includes_drafts_for_bloggers(
    client: TestClient, blogger_headers: dict[str, str], make_post: Callable[..., BlogPost]
) -> None:
    make_post(slug="draft-x", status="draft")
    res = client.get(f"{BASE}/admin/all", headers=blogger_headers)
    assert res.status_code == 200
    assert "draft-x" in [d["slug"] for d in res.json()["documents"]]
