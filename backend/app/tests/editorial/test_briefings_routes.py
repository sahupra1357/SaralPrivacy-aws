"""Routes ported from `app/api/briefings/{generate,approve,send,delete,today}/route.ts`
plus the public/admin read endpoints. Strings are the exact ones in
docs/build/inventory/editorial.md."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.editorial import briefings as logic
from app.models.editorial import Briefing

BASE = "/api/v1/briefings"


# ── GET /briefings/generate ────────────────────────────────────────────────
def test_generate_get_is_gone(client: TestClient) -> None:
    res = client.get(f"{BASE}/generate")
    assert res.status_code == 410
    assert res.json() == {
        "error": "Vercel cron disabled. Use n8n pipeline → POST /api/briefings/generate."
    }


# ── POST /briefings/generate ───────────────────────────────────────────────
def test_generate_rejects_a_wrong_secret(client: TestClient) -> None:
    res = client.post(
        f"{BASE}/generate", json={"title": "x"}, headers={"Authorization": "Bearer nope"}
    )
    assert res.status_code == 401
    assert res.json() == {"error": "Unauthorized."}


def test_generate_rejects_when_no_secret_is_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CRON_SECRET", "")
    res = client.post(f"{BASE}/generate", json={"title": "x"}, headers={"Authorization": "Bearer "})
    assert res.status_code == 401


def test_generate_manual_payload_creates_an_approved_briefing(
    client: TestClient, session: Session, cron_headers: dict[str, str]
) -> None:
    payload = {
        "title": "Are You Breaking This Law?",
        "date": "2026-05-30",
        "excerpt": "Short preview.",
        "summary": "Hook body.",
        "why_it_matters": '{"why":"Hook body."}',
        "action_checklist": ["Do this", "Then that"],
        "category": "fix",
        "tags": ["checklist", "consent"],
        "industries": ["ca-firms"],
        "read_time": 4,
        "infographic_url": "https://cdn.test/inf20260530.jpg",
    }
    res = client.post(f"{BASE}/generate", json=payload, headers=cron_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert body["slug"] == "2026-05-30-are-you-breaking-this-law"

    row = session.exec(select(Briefing).where(Briefing.slug == body["slug"])).one()
    assert str(row.id) == body["briefingId"]
    assert row.status == "approved"
    assert row.author == "DPDPA Editorial Team"
    assert row.featured is False
    assert row.read_time == 4
    assert json.loads(row.action_checklist or "") == ["Do this", "Then that"]
    assert json.loads(row.industries or "") == ["ca-firms"]
    assert row.infographic_base64 == "https://cdn.test/inf20260530.jpg"
    # A backfilled day is stamped 09:00 IST of its roadmap date.
    assert row.created_at_attr == datetime(2026, 5, 30, 3, 30, tzinfo=UTC)
    assert row.approval_token


def test_generate_accepts_the_x_cron_secret_header(client: TestClient, cron_secret: str) -> None:
    res = client.post(
        f"{BASE}/generate", json={"title": "Header Auth"}, headers={"x-cron-secret": cron_secret}
    )
    assert res.status_code == 200


def test_generate_manual_replaces_same_slug_drafts(
    client: TestClient,
    session: Session,
    cron_headers: dict[str, str],
    make_briefing: Callable[..., Briefing],
) -> None:
    make_briefing(slug="2026-05-30-retry-me", status="draft")
    res = client.post(
        f"{BASE}/generate", json={"title": "Retry me", "date": "2026-05-30"}, headers=cron_headers
    )
    assert res.status_code == 200
    rows = session.exec(select(Briefing).where(Briefing.slug == "2026-05-30-retry-me")).all()
    assert [r.status for r in rows] == ["approved"]


def test_generate_manual_packs_business_impact_into_the_envelope(
    client: TestClient, session: Session, cron_headers: dict[str, str]
) -> None:
    res = client.post(
        f"{BASE}/generate",
        json={
            "title": "Envelope",
            "why_it_matters": "W",
            "business_impact": "I",
            "who_is_affected": ["HR"],
        },
        headers=cron_headers,
    )
    row = session.exec(select(Briefing).where(Briefing.slug == res.json()["slug"])).one()
    assert json.loads(row.why_it_matters or "") == {"why": "W", "impact": "I", "affected": ["HR"]}
    assert row.category == "compliance-guidance"


def test_generate_without_title_auto_generates_and_emails_admins(
    client: TestClient,
    session: Session,
    cron_headers: dict[str, str],
    mock_llm: Any,
    mock_email: Any,
) -> None:
    mock_llm.responses["complete"] = json.dumps(
        {
            "title": "What Happens If You Share Customer Data?",
            "hook_line1": "One leak can cost you.",
            "save_line": "Permission first, always.",
            "action_items": ["a", "b", "c"],
            "excerpt": "Two sentences.",
            "category": "consent-management",
            "tags": ["t"],
            "industries": ["general"],
        }
    )
    res = client.post(f"{BASE}/generate?forDate=2026-01-04", content=b"{}", headers=cron_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "2026-01-04-what-happens-if-you-share-customer-data"
    assert body["topic"] == logic.DPDPA_TOPICS[3]["topic"]  # day-of-year 3

    call = mock_llm.last()
    assert call["model"] == "claude-opus-4-5"
    assert call["max_tokens"] == 2048
    assert "Weekly Recap" in call["messages"][0]["content"]  # 2026-01-04 is a Sunday

    row = session.exec(select(Briefing).where(Briefing.slug == body["slug"])).one()
    assert json.loads(row.why_it_matters or "")["version"] == 2
    assert row.summary == "Permission first, always."
    assert mock_email.calls and mock_email.last()["subject"].startswith("New Briefing LIVE:")


def test_generate_returns_500_when_claude_output_is_not_json(
    client: TestClient, cron_headers: dict[str, str], mock_llm: Any
) -> None:
    mock_llm.responses["complete"] = "not json"
    res = client.post(f"{BASE}/generate", json={}, headers=cron_headers)
    assert res.status_code == 500
    assert res.json() == {"error": "Failed to generate briefing."}


# ── GET /briefings/approve ─────────────────────────────────────────────────
def test_approve_without_params_redirects_with_missing_params(client: TestClient) -> None:
    res = client.get(f"{BASE}/approve", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"].endswith("/admin?briefing=error&reason=missing-params")


def test_approve_with_a_bad_token_redirects_invalid_token(
    client: TestClient, make_briefing: Callable[..., Briefing]
) -> None:
    b = make_briefing()
    res = client.get(f"{BASE}/approve?token=wrong&briefingId={b.id}", follow_redirects=False)
    assert res.headers["location"].endswith("/admin?briefing=error&reason=invalid-token")


def test_approve_an_already_sent_briefing_redirects_already_sent(
    client: TestClient, make_briefing: Callable[..., Briefing], broadcast: Any
) -> None:
    b = make_briefing(status="sent")
    res = client.get(f"{BASE}/approve?token=tok-123&briefingId={b.id}", follow_redirects=False)
    assert res.headers["location"].endswith("/admin?briefing=already-sent")
    assert broadcast.calls == []


def test_approve_broadcasts_and_marks_sent(
    client: TestClient, session: Session, make_briefing: Callable[..., Briefing], broadcast: Any
) -> None:
    b = make_briefing()
    res = client.get(f"{BASE}/approve?token=tok-123&briefingId={b.id}", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "https://saralprivacy.test/admin?briefing=published&sent=2"
    assert broadcast.calls[0]["briefing"]["title"] == b.title
    session.refresh(b)
    assert b.status == "sent" and b.subscriber_count == 2 and b.sent_at is not None


def test_approve_accepts_a_legacy_appwrite_id(
    client: TestClient,
    make_briefing: Callable[..., Briefing],
    broadcast: Any,  # noqa: ARG001
) -> None:
    make_briefing(legacy_id="65f0abc123")
    res = client.get(f"{BASE}/approve?token=tok-123&briefingId=65f0abc123", follow_redirects=False)
    assert "briefing=published" in res.headers["location"]


def test_approve_unknown_briefing_redirects_server_error(client: TestClient) -> None:
    res = client.get(
        f"{BASE}/approve?token=t&briefingId=00000000-0000-0000-0000-000000000000",
        follow_redirects=False,
    )
    assert res.headers["location"].endswith("/admin?briefing=error&reason=server-error")


# ── POST /briefings/send ───────────────────────────────────────────────────
def test_send_requires_admin(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.post(f"{BASE}/send", json={"briefingId": "x"}).status_code == 401
    assert (
        client.post(f"{BASE}/send", json={"briefingId": "x"}, headers=blogger_headers).status_code
        == 403
    )


def test_send_requires_a_briefing_id(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(f"{BASE}/send", json={}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "briefingId is required."}


def test_send_unknown_briefing_is_404(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(
        f"{BASE}/send",
        json={"briefingId": "00000000-0000-0000-0000-000000000000"},
        headers=admin_headers,
    )
    assert res.status_code == 404
    assert res.json() == {"error": "Briefing not found"}


def test_send_only_approved_briefings(
    client: TestClient, admin_headers: dict[str, str], make_briefing: Callable[..., Briefing]
) -> None:
    b = make_briefing(status="draft")
    res = client.post(f"{BASE}/send", json={"briefingId": str(b.id)}, headers=admin_headers)
    assert res.status_code == 400
    assert res.json() == {
        "error": 'Briefing status is "draft". Only approved briefings can be sent.'
    }


def test_send_broadcasts_and_reports_counts(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    make_briefing: Callable[..., Briefing],
    broadcast: Any,
) -> None:
    broadcast.failed = 1
    b = make_briefing()
    res = client.post(f"{BASE}/send", json={"briefingId": str(b.id)}, headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"success": True, "sent": 1, "failed": 1, "total": 2}
    session.refresh(b)
    assert b.status == "sent" and b.subscriber_count == 1


# ── DELETE /briefings/delete ───────────────────────────────────────────────
def test_delete_requires_the_briefing_secret(client: TestClient) -> None:
    res = client.delete(f"{BASE}/delete?id=x", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401
    assert res.json() == {"error": "Unauthorized"}


def test_delete_requires_an_id(client: TestClient, cron_headers: dict[str, str]) -> None:
    res = client.delete(f"{BASE}/delete", headers=cron_headers)
    assert res.status_code == 400
    assert res.json() == {"error": "Missing ?id= parameter"}


def test_delete_removes_the_row(
    client: TestClient,
    session: Session,
    cron_headers: dict[str, str],
    make_briefing: Callable[..., Briefing],
) -> None:
    b = make_briefing()
    bid = str(b.id)
    res = client.delete(f"{BASE}/delete?id={bid}", headers=cron_headers)
    assert res.json() == {"success": True, "deleted": bid}
    session.expire_all()
    assert session.get(Briefing, b.id) is None


def test_delete_of_a_missing_row_is_a_no_op(
    client: TestClient, cron_headers: dict[str, str]
) -> None:
    res = client.delete(f"{BASE}/delete?id=legacy-gone", headers=cron_headers)
    assert res.status_code == 200


# ── GET /briefings/today ───────────────────────────────────────────────────
def test_today_requires_the_cron_secret(client: TestClient) -> None:
    res = client.get(f"{BASE}/today")
    assert res.status_code == 401
    assert res.json() == {"error": "Unauthorized."}


def test_today_404_when_nothing_published_today(
    client: TestClient, cron_headers: dict[str, str]
) -> None:
    res = client.get(f"{BASE}/today", headers=cron_headers)
    assert res.status_code == 404
    body = res.json()
    assert body["error"] == "No briefing found for today."
    assert body["hint"] == "Pipeline may not have run yet, or no topic was planned for today."
    assert len(body["date"]) == 10


def test_today_returns_the_n8n_shape_with_v1_fallbacks(
    client: TestClient, cron_headers: dict[str, str], make_briefing: Callable[..., Briefing]
) -> None:
    now = datetime.now(UTC)
    make_briefing(
        slug="today-slug",
        why_it_matters=json.dumps({"why_heading": "Heading", "impact": "Impact", "save": "Save"}),
        action_checklist=json.dumps(["one", {"action": "two"}, {"x": 1}]),
        created_at_attr=now,
        read_time=None,
    )
    make_briefing(slug="yesterday", created_at_attr=now - timedelta(days=2))
    res = client.get(f"{BASE}/today", headers=cron_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "today-slug"
    assert body["url"] == "https://saralprivacy.test/briefings/today-slug"
    assert body["hook_line1"] == "Heading"
    assert body["card_why"] == "Impact"
    assert body["save_line"] == "Save"
    assert body["action_items"] == ["one", "two", '{"x":1}']
    assert body["read_time"] == 3
    assert body["author"] == "DPDPA Editorial Team"


# ── public + admin reads ───────────────────────────────────────────────────
def test_public_list_hides_drafts_and_approval_tokens(
    client: TestClient, make_briefing: Callable[..., Briefing]
) -> None:
    make_briefing(slug="live-one")
    make_briefing(slug="draft-one", status="draft")
    res = client.get(BASE)
    assert res.status_code == 200
    body = res.json()
    slugs = [d["slug"] for d in body["docs"]]
    assert "live-one" in slugs and "draft-one" not in slugs
    doc = next(d for d in body["docs"] if d["slug"] == "live-one")
    assert "approval_token" not in doc
    assert doc["$id"] == doc["id"]
    assert doc["created_at"].startswith("2026-09-18T03:30")


def test_by_slug_returns_the_document_or_404(
    client: TestClient, make_briefing: Callable[..., Briefing]
) -> None:
    make_briefing(slug="find-me")
    assert client.get(f"{BASE}/by-slug/find-me").json()["slug"] == "find-me"
    res = client.get(f"{BASE}/by-slug/nope")
    assert res.status_code == 404
    assert res.json() == {"error": "Not found"}


def test_admin_list_needs_admin_and_includes_drafts(
    client: TestClient,
    admin_headers: dict[str, str],
    blogger_headers: dict[str, str],
    make_briefing: Callable[..., Briefing],
) -> None:
    make_briefing(slug="draft-two", status="draft")
    assert client.get(f"{BASE}/admin/all", headers=blogger_headers).status_code == 403
    res = client.get(f"{BASE}/admin/all", headers=admin_headers)
    assert res.status_code == 200
    assert "draft-two" in [d["slug"] for d in res.json()["documents"]]
