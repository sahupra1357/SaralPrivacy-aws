"""app/crud/editorial.py — one test per query."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from app.crud import editorial as crud
from app.models.editorial import BlogPost, Briefing


def test_get_briefing_by_uuid_legacy_id_and_slug(
    session: Session, make_briefing: Callable[..., Briefing]
) -> None:
    b = make_briefing(legacy_id="65abc", slug="by-slug")
    assert crud.get_briefing(session, str(b.id)) == b
    assert crud.get_briefing(session, "65abc") == b
    assert crud.get_briefing(session, "missing") is None
    assert crud.get_briefing_by_slug(session, "by-slug") == b


def test_list_briefings_filters_status_and_counts(
    session: Session, make_briefing: Callable[..., Briefing]
) -> None:
    make_briefing(slug="l1")
    make_briefing(slug="l2", status="sent")
    make_briefing(slug="l3", status="draft")
    rows, total = crud.list_briefings(session)
    assert {r.slug for r in rows} >= {"l1", "l2"} and "l3" not in {r.slug for r in rows}
    assert total == len(rows)
    everything, all_total = crud.list_briefings(session, statuses=None)
    assert all_total == total + 1 and len(everything) == all_total


def test_find_and_delete_briefings(
    session: Session, make_briefing: Callable[..., Briefing]
) -> None:
    b = make_briefing(slug="dupe", status="draft")
    assert crud.find_briefings(session, slug="dupe", status="draft") == [b]
    crud.delete_briefing(session, b)
    assert crud.find_briefings(session, slug="dupe", status="draft") == []


def test_published_briefing_for_date_exists(
    session: Session, make_briefing: Callable[..., Briefing]
) -> None:
    make_briefing(slug="2026-05-01-draft", status="draft")
    assert crud.published_briefing_for_date_exists(session, "2026-05-01") is False
    make_briefing(slug="2026-05-01-live")
    assert crud.published_briefing_for_date_exists(session, "2026-05-01") is True


def test_latest_briefing_between(session: Session, make_briefing: Callable[..., Briefing]) -> None:
    t = datetime(2026, 3, 1, 4, tzinfo=UTC)
    make_briefing(slug="older", created_at_attr=t)
    newer = make_briefing(slug="newer", created_at_attr=t + timedelta(hours=1))
    make_briefing(slug="draft", status="draft", created_at_attr=t + timedelta(hours=2))
    assert (
        crud.latest_briefing_between(session, t - timedelta(hours=1), t + timedelta(hours=5))
        == newer
    )
    assert (
        crud.latest_briefing_between(session, t + timedelta(days=1), t + timedelta(days=2)) is None
    )


def test_update_briefing_sets_fields_and_bumps_updated_at(
    session: Session, make_briefing: Callable[..., Briefing]
) -> None:
    b = make_briefing()
    before = b.updated_at
    crud.update_briefing(session, b, status="sent", subscriber_count=5)
    session.refresh(b)
    assert b.status == "sent" and b.subscriber_count == 5 and b.updated_at >= before


def test_blog_post_queries(session: Session, make_post: Callable[..., BlogPost]) -> None:
    p = make_post(slug="p1", lane="myth-fact", legacy_id="old-1")
    make_post(slug="p2", lane="myth-fact", status="draft")
    assert crud.get_blog_post(session, "old-1") == p
    assert crud.get_published_post_by_slug(session, "p1") == p
    assert crud.get_published_post_by_slug(session, "p2") is None
    rows, total = crud.list_blog_posts(session, lane="myth-fact")
    assert [r.slug for r in rows] == ["p1"] and total == 1
    rows, _ = crud.list_blog_posts(session, status=None, lane="myth-fact", order="updated")
    assert {r.slug for r in rows} == {"p1", "p2"}


def test_create_and_update_blog_post(session: Session) -> None:
    post = crud.create_blog_post(
        session,
        BlogPost(
            title="T", slug="s", excerpt="e", lane="law-explained", author="A", status="draft"
        ),
    )
    crud.update_blog_post(session, post, {"status": "published", "published_at": "2026-09-18"})
    session.refresh(post)
    assert post.status == "published" and post.published_at == "2026-09-18"
