"""Verified blog: editor save/validate/revise/infographic (admin + blogger) and public reads.

Ports `frontend/app/api/blog/{save,validate,revise,infographic,[id]}/route.ts`. Bodies keep
the `{error: ...}` shape the BlogEditor reads. After every write the public blog ISR is
busted (`/blog/<slug>`, `/blog`, tag `blog-posts`); a purge failure never turns a
successful write into an error (the admin would retry and duplicate the post).
"""

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import SessionDep, require_role
from app.crud import editorial as crud
from app.editorial import blog_ai, infographic
from app.editorial.docs import blog_doc, js_json, js_round, word_count
from app.models.editorial import BlogPost
from app.services import revalidate, storage

log = logging.getLogger(__name__)

router = APIRouter(prefix="/blog", tags=["blog"])

Editor = Depends(require_role("admin", "blogger"))


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def safe_revalidate_blog(slug: str | None) -> None:
    try:
        if slug:
            revalidate.path(f"/blog/{slug}")
        revalidate.path("/blog")
        revalidate.tag("blog-posts")
    except Exception as e:  # noqa: BLE001
        log.error("[blog revalidate] %s", e)


# ── public reads ────────────────────────────────────────────────────────────
class DocsOut(BaseModel):
    docs: list[dict[str, Any]]
    total: int


class AdminDocsOut(BaseModel):
    documents: list[dict[str, Any]]
    total: int


@router.get("", response_model=DocsOut)
def list_published(
    session: SessionDep,
    lane: str | None = None,
    exclude_slug: str | None = None,
    order: Literal["created", "updated"] = "created",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocsOut:
    rows, total = crud.list_blog_posts(
        session,
        status="published",
        lane=lane if lane and lane != "all" else None,
        exclude_slug=exclude_slug,
        order=order,
        limit=limit,
        offset=offset,
    )
    return DocsOut(docs=[blog_doc(r) for r in rows], total=total)


@router.get("/by-slug/{slug}", response_model=None)
def get_published_by_slug(slug: str, session: SessionDep) -> dict[str, Any] | JSONResponse:
    row = crud.get_published_post_by_slug(session, slug)
    if row is None:
        return _err("Not found", 404)
    return blog_doc(row)


@router.get("/admin/all", response_model=AdminDocsOut, dependencies=[Editor])
def list_for_admin(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=500)] = 100
) -> AdminDocsOut:
    rows, total = crud.list_blog_posts(session, status=None, limit=limit)
    return AdminDocsOut(documents=[blog_doc(r) for r in rows], total=total)


# ── save ────────────────────────────────────────────────────────────────────
class SaveIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # scope_labels, infographic_url, … pass through unused

    id: str | None = None
    title: str = ""
    slug: str = ""
    excerpt: str = ""
    lane: str = ""
    author: str = ""
    tags: str | None = ""
    featured: bool = False
    status: str = "draft"
    section_what_changed: str | None = ""
    section_law_says: str | None = ""
    section_do_now: str | None = ""
    section_uncertain: str | None = ""
    section_mistakes: str | None = ""
    primary_sources: list[Any] = []
    validated_at: str | None = ""
    published_at: str | None = None
    score_legal_accuracy: float = 0
    score_primary_source: float = 0
    score_currency: float = 0
    score_scope: float = 0
    score_operational: float = 0


def normalize_slug(raw: str) -> str:
    """Bare URL segment: a leading/trailing "/" would make the stored slug differ from the
    public URL, so the exact-match lookup would 404 a post the list still shows."""
    return raw.strip().strip("/").strip()


def norm_section(s: str | None) -> str | None:
    if not s:
        return None
    t = s.strip()
    return None if t == "" or t.lower() == "blank" else t


def build_document(p: SaveIn, *, today: str | None = None) -> dict[str, Any]:
    do_now, uncertain, mistakes = (
        norm_section(p.section_do_now),
        norm_section(p.section_uncertain),
        norm_section(p.section_mistakes),
    )
    what_changed, law_says = norm_section(p.section_what_changed), norm_section(p.section_law_says)
    scores = [
        js_round(p.score_legal_accuracy),
        js_round(p.score_primary_source),
        js_round(p.score_currency),
        js_round(p.score_scope),
        js_round(p.score_operational),
    ]
    total_words = (
        word_count(p.section_what_changed or "")
        + word_count(p.section_law_says or "")
        + word_count(do_now or "")
        + word_count(uncertain or "")
        + word_count(mistakes or "")
    )
    doc: dict[str, Any] = {
        "title": p.title[:200],
        "slug": normalize_slug(p.slug)[:200],
        "excerpt": p.excerpt[:600],
        "lane": p.lane[:60],
        "author": p.author[:100],
        "tags": p.tags[:500] if p.tags else None,
        "featured": p.featured,
        "status": p.status,
        "section_what_changed": what_changed[:10000] if what_changed else None,
        "section_law_says": law_says[:10000] if law_says else None,
        # Overflow sections packed into one JSON column (Appwrite byte-limit legacy).
        "sections_json": js_json(
            {
                "section_do_now": do_now,
                "section_uncertain": uncertain,
                "section_mistakes": mistakes,
                "primary_sources": js_json(p.primary_sources),
            }
        ),
        "validated_at": p.validated_at[:30] if p.validated_at else None,
        "score_legal_accuracy": scores[0],
        "score_primary_source": scores[1],
        "score_currency": scores[2],
        "score_scope": scores[3],
        "score_operational": scores[4],
        "validation_score": sum(scores),
        "read_time": max(1, js_round(total_words / 200)),
    }
    if p.status == "published":
        published = p.published_at or today or datetime.now(UTC).date().isoformat()
        doc["published_at"] = published[:30]
    return doc


@router.post("/save", response_model=None, dependencies=[Editor])
def create_post(body: SaveIn, session: SessionDep) -> JSONResponse:
    if not body.title or not body.slug:
        return _err("Title and slug are required", 400)
    if not normalize_slug(body.slug):
        return _err("Slug cannot be only slashes or whitespace", 400)
    try:
        doc = build_document(body)
        post = crud.create_blog_post(session, BlogPost(**doc))
    except Exception as e:
        log.exception("[blog/save POST]")
        return _err(str(e) or "Unknown error", 500)
    safe_revalidate_blog(post.slug)
    return JSONResponse({"success": True, "id": str(post.id), "slug": post.slug})


@router.patch("/save", response_model=None, dependencies=[Editor])
def update_post(body: SaveIn, session: SessionDep) -> JSONResponse:
    if not body.id:
        return _err("Document ID required for update", 400)
    if body.slug and not normalize_slug(body.slug):
        return _err("Slug cannot be only slashes or whitespace", 400)
    try:
        post = crud.get_blog_post(session, body.id)
        if post is None:
            return _err("Not found", 404)
        doc = build_document(body)
        crud.update_blog_post(session, post, doc)
    except Exception as e:
        log.exception("[blog/save PATCH]")
        return _err(str(e) or "Unknown error", 500)
    safe_revalidate_blog(doc["slug"])
    return JSONResponse({"success": True, "id": body.id, "slug": doc["slug"]})


# ── validate ────────────────────────────────────────────────────────────────
class ValidateIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str | None = None
    lane: str | None = None
    section_what_changed: str | None = None
    section_law_says: str | None = None
    section_do_now: str | None = None
    section_uncertain: str | None = None
    section_mistakes: str | None = None


@router.post("/validate", response_model=None, dependencies=[Editor])
def validate(body: ValidateIn) -> JSONResponse:
    if not body.title or not body.section_what_changed:
        return _err("Title and at least one content section required", 400)
    try:
        output = blog_ai.run_validation(
            blog_ai.validate_user_content(
                body.title,
                body.lane,
                body.section_what_changed,
                body.section_law_says,
                body.section_do_now,
                body.section_uncertain,
                body.section_mistakes,
            )
        )
        if output is None:
            return _err(
                "Validation model did not return a structured response. Please try again.", 502
            )
        return JSONResponse({**output.model_dump(), "validated_at": blog_ai.ist_date()})
    except Exception as e:
        log.exception("[blog/validate POST]")
        return _err(str(e) or "Validation failed", 500)


# ── revise ──────────────────────────────────────────────────────────────────
class ReviseIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    sectionKey: str | None = None  # noqa: N815
    currentContent: str | None = None  # noqa: N815
    feedbackNote: str | None = None  # noqa: N815
    title: str | None = None
    section_what_changed: str | None = None
    section_law_says: str | None = None
    section_do_now: str | None = None
    section_uncertain: str | None = None
    section_mistakes: str | None = None


@router.post("/revise", response_model=None, dependencies=[Editor])
def revise(body: ReviseIn) -> JSONResponse:
    if not body.sectionKey or not body.currentContent or not body.feedbackNote:
        return _err("sectionKey, currentContent, and feedbackNote are required", 400)
    try:
        prompt = blog_ai.revise_prompt(
            section_key=body.sectionKey,
            current_content=body.currentContent,
            feedback_note=body.feedbackNote,
            title=body.title,
            sections={
                "section_what_changed": body.section_what_changed,
                "section_law_says": body.section_law_says,
                "section_do_now": body.section_do_now,
                "section_uncertain": body.section_uncertain,
                "section_mistakes": body.section_mistakes,
            },
        )
        text = blog_ai.run_revision(prompt)
        if not text or not text.strip():
            return _err("Revision model returned empty content. Please try again.", 502)
        return JSONResponse({"revisedContent": text.strip()})
    except Exception as e:
        log.exception("[blog/revise POST]")
        return _err(str(e) or "Revision failed", 500)


# ── infographic ─────────────────────────────────────────────────────────────
class InfographicIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    title: str | None = None
    lane: str | None = None
    excerpt: str | None = None
    section_what_changed: str | None = None
    section_law_says: str | None = None
    section_do_now: str | None = None


@router.post("/infographic", response_model=None, dependencies=[Editor])
def generate_infographic(body: InfographicIn, session: SessionDep) -> JSONResponse:
    if not body.id or not body.title:
        return _err("id and title are required", 400)
    try:
        post = crud.get_blog_post(session, body.id)
        if post is None:
            return _err("Not found", 404)
        svg = infographic.build_svg(
            infographic.InfographicInput(
                title=body.title,
                lane=body.lane or "",
                excerpt=body.excerpt,
                section_what_changed=body.section_what_changed,
                section_law_says=body.section_law_says,
                section_do_now=body.section_do_now,
            )
        )
        png = infographic.rasterize_png(svg)
        # Deterministic key per post so re-generation replaces in place; `?v=` busts caches.
        url = storage.put(f"infographics/blog_inf_{body.id}.png", png, "image/png")
        public_url = f"{url}?v={int(time.time() * 1000)}"
        crud.update_blog_post(session, post, {"infographic_url": public_url})
    except Exception as e:
        log.exception("[blog/infographic POST]")
        return _err(str(e) or "Infographic generation failed", 500)
    safe_revalidate_blog(post.slug)
    return JSONResponse({"success": True, "url": public_url})


# ── one post (editor) — declared last so the fixed paths above win ─────────
@router.get("/{id}", response_model=None, dependencies=[Editor])
def get_post(id: str, session: SessionDep) -> dict[str, Any] | JSONResponse:  # noqa: A002
    post = crud.get_blog_post(session, id)
    if post is None:
        return _err("Not found", 404)
    return blog_doc(post)
