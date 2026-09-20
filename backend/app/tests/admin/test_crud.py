"""crud/admin.py — one test per query."""

import uuid
from datetime import UTC, date, datetime

from sqlmodel import Session

from app.crud import admin as crud
from app.models.admin import AiCitation, SeoInspection, SeoRun


def test_row_to_doc_maps_renamed_columns_and_system_fields() -> None:
    rid = uuid.uuid4()
    created = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    doc = crud.row_to_doc(
        "email_send_log",
        {"id": rid, "created_at": created, "updated_at": created, "updated_at_attr": "x", "n": 1},
    )
    assert doc["id"] == doc["$id"] == str(rid)
    assert doc["updated_at"] == "x"
    assert "updated_at_attr" not in doc
    assert doc["$createdAt"] == "2026-09-01T10:00:00.000Z"
    assert doc["$updatedAt"] == "2026-09-01T10:00:00.000Z"
    assert doc["created_at"] == "2026-09-01T10:00:00.000Z"


def test_json_safe_handles_postgres_types() -> None:
    from decimal import Decimal

    out = crud.json_safe({"d": date(2026, 1, 2), "n": Decimal("1.5"), "l": [uuid.UUID(int=1)]})
    assert out == {"d": "2026-01-02", "n": 1.5, "l": ["00000000-0000-0000-0000-000000000001"]}


def test_allowlist_is_the_19_collections() -> None:
    assert len(crud.ALLOWED_COLLECTIONS) == 19
    assert {
        "outreach_contacts",
        "ai_citations",
        "blogger_accounts",
        "briefings",
    } <= crud.ALLOWED_COLLECTIONS


def test_blogger_crud_round_trip(session: Session) -> None:
    row = crud.create_blogger(session, email="w@x.test", name="W", bio="", created_at_attr="t")
    assert crud.get_blogger_by_email(session, "W@X.test ") is not None
    assert crud.resolve_blogger(session, str(row.id)) is not None
    assert crud.resolve_blogger(session, "legacy-missing") is None
    crud.set_blogger_active(session, row, True)
    assert row.active is True and row.invite_token == ""
    assert [b.id for b in crud.list_bloggers(session)] == [row.id]
    crud.delete_blogger(session, row)
    assert crud.list_bloggers(session) == []


def test_insert_citation(session: Session) -> None:
    row = crud.insert_citation(
        session,
        AiCitation(
            run_id="r",
            date="2026-09-14",
            week_num=38,
            engine="claude",
            engine_label="Claude",
            query_id="Q1",
            query_text="q",
            cited="No",
        ),
    )
    assert row.id is not None and row.created_at is not None


def test_seo_run_queries(session: Session) -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)
    run = SeoRun(run_at=now, site="s", scope="full", verdict_code="TOO_EARLY", sitemap_urls=["a"])
    dry = SeoRun(run_at=now, site="s", scope="full", verdict_code="TOO_EARLY", dry_run=True)
    crud.insert_seo_run(
        session,
        run,
        [
            SeoInspection(
                run_id=run.id, run_at=now, url=f"https://x/{p}", path=f"/{p}", bucket="indexed"
            )
            for p in "ba"
        ],
        page=1,
    )
    crud.insert_seo_run(session, dry, [])

    assert crud.latest_seo_run(session).id == run.id  # type: ignore[union-attr]
    assert [r.id for r in crud.list_seo_runs(session)] == [run.id]
    assert [i.path for i in crud.list_seo_inspections(session, run.id)] == ["/a", "/b"]
    assert [i.url for i in crud.list_seo_inspections(session, run.id, order_by="url")] == [
        "https://x/a",
        "https://x/b",
    ]


def test_index_request_upsert_and_order(session: Session) -> None:
    crud.upsert_index_request(session, "https://saralprivacy.com/a", date(2026, 8, 1), None)
    crud.upsert_index_request(session, "https://saralprivacy.com/b", date(2026, 9, 1), "n")
    crud.upsert_index_request(session, "https://saralprivacy.com/a", date(2026, 9, 2), "again")
    rows = crud.list_index_requests(session)
    assert [(r.url[-1], r.requested_at, r.note) for r in rows] == [
        ("a", date(2026, 9, 2), "again"),
        ("b", date(2026, 9, 1), "n"),
    ]


def test_task_lifecycle(session: Session) -> None:
    task = crud.create_task(session, "aeo-panel", None)
    assert task.status == "running" and task.finished_at is None
    done = crud.finish_task(
        session, task.id, ok=False, result={"ok": False, "at": datetime(2026, 1, 1, tzinfo=UTC)}
    )
    assert done is not None and done.status == "failed"
    assert done.result == {"ok": False, "at": "2026-01-01T00:00:00.000Z"}
    assert crud.get_task(session, task.id) is not None
    assert crud.finish_task(session, uuid.uuid4(), ok=True, result={}) is None
