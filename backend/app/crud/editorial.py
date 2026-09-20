"""Queries for the editorial module (briefings + blog). No HTTP concepts, no business rules.

Ids: the API accepts a row uuid or an Appwrite-era `$id` (stored as `legacy_id`), exactly as
`lib/db` resolved them, so ids in old approval emails and admin bookmarks keep working.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func
from sqlmodel import Session, SQLModel, col, select

from app.models.base import utcnow
from app.models.editorial import PUBLISHED_BRIEFING_STATUSES, BlogPost, Briefing


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None


def _get[M: (Briefing, BlogPost)](session: Session, model: type[M], id_or_legacy: str) -> M | None:
    parsed = _as_uuid(id_or_legacy)
    if parsed is not None:
        return session.get(model, parsed)
    return session.exec(select(model).where(model.legacy_id == id_or_legacy).limit(1)).first()


def _save(session: Session, row: SQLModel) -> None:
    session.add(row)
    session.commit()
    session.refresh(row)


def _count(session: Session, stmt: Any) -> int:
    return int(session.exec(select(func.count()).select_from(stmt.subquery())).one())


# ── briefings ──────────────────────────────────────────────────────────────
def get_briefing(session: Session, id_or_legacy: str) -> Briefing | None:
    return _get(session, Briefing, id_or_legacy)


def get_briefing_by_slug(session: Session, slug: str) -> Briefing | None:
    return session.exec(select(Briefing).where(Briefing.slug == slug).limit(1)).first()


def list_briefings(
    session: Session,
    *,
    statuses: Sequence[str] | None = PUBLISHED_BRIEFING_STATUSES,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Briefing], int]:
    """Newest first by row creation (`$createdAt`), as every caller ordered."""
    stmt = select(Briefing)
    if statuses is not None:
        stmt = stmt.where(col(Briefing.status).in_(list(statuses)))
    total = _count(session, stmt)
    rows = session.exec(
        stmt.order_by(col(Briefing.created_at).desc()).offset(offset).limit(limit)
    ).all()
    return list(rows), total


def list_all_briefings(session: Session) -> list[Briefing]:
    return list(session.exec(select(Briefing).order_by(col(Briefing.created_at).asc())).all())


def find_briefings(session: Session, *, slug: str, status: str, limit: int = 5) -> list[Briefing]:
    stmt = select(Briefing).where(Briefing.slug == slug, Briefing.status == status).limit(limit)
    return list(session.exec(stmt).all())


def published_briefing_for_date_exists(session: Session, date_str: str) -> bool:
    """An approved/sent briefing whose slug carries this date prefix (pipeline double-run guard)."""
    stmt = (
        select(Briefing.id)
        .where(col(Briefing.slug).startswith(f"{date_str}-"))
        .where(col(Briefing.status).in_(list(PUBLISHED_BRIEFING_STATUSES)))
        .limit(1)
    )
    return session.exec(stmt).first() is not None


def latest_briefing_between(session: Session, start: datetime, end: datetime) -> Briefing | None:
    """Newest approved/sent briefing whose app-level `created_at` falls in [start, end]."""
    stmt = (
        select(Briefing)
        .where(col(Briefing.status).in_(list(PUBLISHED_BRIEFING_STATUSES)))
        .where(col(Briefing.created_at_attr) >= start)
        .where(col(Briefing.created_at_attr) <= end)
        .order_by(col(Briefing.created_at_attr).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def create_briefing(session: Session, briefing: Briefing) -> Briefing:
    _save(session, briefing)
    return briefing


def update_briefing(session: Session, briefing: Briefing, **fields: Any) -> Briefing:
    for key, value in fields.items():
        setattr(briefing, key, value)
    briefing.updated_at = utcnow()
    _save(session, briefing)
    return briefing


def delete_briefing(session: Session, briefing: Briefing) -> None:
    session.delete(briefing)
    session.commit()


# ── blog posts ─────────────────────────────────────────────────────────────
def get_blog_post(session: Session, id_or_legacy: str) -> BlogPost | None:
    return _get(session, BlogPost, id_or_legacy)


def get_published_post_by_slug(session: Session, slug: str) -> BlogPost | None:
    stmt = select(BlogPost).where(BlogPost.slug == slug, BlogPost.status == "published").limit(1)
    return session.exec(stmt).first()


def list_blog_posts(
    session: Session,
    *,
    status: str | None = "published",
    lane: str | None = None,
    exclude_slug: str | None = None,
    order: Literal["created", "updated"] = "created",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BlogPost], int]:
    stmt = select(BlogPost)
    if status is not None:
        stmt = stmt.where(BlogPost.status == status)
    if lane:
        stmt = stmt.where(BlogPost.lane == lane)
    if exclude_slug:
        stmt = stmt.where(BlogPost.slug != exclude_slug)
    total = _count(session, stmt)
    order_col = BlogPost.updated_at if order == "updated" else BlogPost.created_at
    rows = session.exec(stmt.order_by(col(order_col).desc()).offset(offset).limit(limit)).all()
    return list(rows), total


def create_blog_post(session: Session, post: BlogPost) -> BlogPost:
    _save(session, post)
    return post


def update_blog_post(session: Session, post: BlogPost, fields: dict[str, Any]) -> BlogPost:
    for key, value in fields.items():
        setattr(post, key, value)
    post.updated_at = utcnow()
    _save(session, post)
    return post


__all__ = [
    "create_blog_post",
    "create_briefing",
    "delete_briefing",
    "find_briefings",
    "get_blog_post",
    "get_briefing",
    "get_briefing_by_slug",
    "get_published_post_by_slug",
    "latest_briefing_between",
    "list_all_briefings",
    "list_blog_posts",
    "list_briefings",
    "published_briefing_for_date_exists",
    "update_blog_post",
    "update_briefing",
]
