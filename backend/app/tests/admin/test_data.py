"""GET /admin/data (allowlisted reader) and GET /admin/dashboard."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

URL = "/api/v1/admin/data"


def _lead(
    insert_row: Callable[..., dict[str, Any]], name: str, source: str, **kw: Any
) -> dict[str, Any]:
    return insert_row(
        "ops.leads",
        name=name,
        email=f"{name.lower()}@example.com",
        company="Acme",
        source=source,
        **kw,
    )


def test_data_requires_a_session(client: TestClient) -> None:
    res = client.get(URL, params={"collection": "leads"})
    assert res.status_code == 401


def test_data_is_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    res = client.get(URL, params={"collection": "leads"}, headers=blogger_headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Access denied."


def test_data_rejects_a_collection_outside_the_allowlist(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    for bad in ("users", "", "leads;drop table ops.leads", "seo_runs"):
        res = client.get(URL, params={"collection": bad}, headers=admin_headers)
        assert res.status_code == 400
        assert res.json()["detail"] == "Invalid collection"


def test_data_returns_documents_newest_first_with_appwrite_names(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    old = _lead(
        insert_row,
        "Old",
        "contact",
        created_at=datetime.now(UTC) - timedelta(days=2),
        created_at_attr="2026-01-01T00:00:00.000Z",
    )
    new = _lead(insert_row, "New", "contact", created_at_attr="2026-09-01T00:00:00.000Z")

    res = client.get(URL, params={"collection": "leads"}, headers=admin_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    first, second = body["documents"]
    assert first["$id"] == first["id"] == str(new["id"])
    assert second["$id"] == str(old["id"])
    # created_at_attr is surfaced under its Appwrite attribute name…
    assert first["created_at"] == "2026-09-01T00:00:00.000Z"
    assert "created_at_attr" not in first
    # …and the system column as $createdAt.
    assert first["$createdAt"].startswith(new["created_at"].date().isoformat())
    assert "$updatedAt" in first


def test_data_filters_by_source_and_status(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    _lead(insert_row, "Consult", "consultation")
    _lead(insert_row, "Contact", "contact")

    res = client.get(
        URL, params={"collection": "leads", "source": "consultation"}, headers=admin_headers
    )

    assert res.status_code == 200
    assert [d["name"] for d in res.json()["documents"]] == ["Consult"]
    assert res.json()["total"] == 1


def test_data_caps_the_limit_at_500_and_defaults_to_200(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    for i in range(3):
        _lead(insert_row, f"L{i}", "contact")

    capped = client.get(URL, params={"collection": "leads", "limit": "9999"}, headers=admin_headers)
    one = client.get(URL, params={"collection": "leads", "limit": "1"}, headers=admin_headers)
    junk = client.get(URL, params={"collection": "leads", "limit": "abc"}, headers=admin_headers)

    assert len(capped.json()["documents"]) == 3
    assert len(one.json()["documents"]) == 1
    assert one.json()["total"] == 3
    assert len(junk.json()["documents"]) == 3


def test_data_answers_500_with_the_generic_message_on_a_query_error(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    # ops.downloads has no `status` column — the query fails, as PostgREST did.
    res = client.get(URL, params={"collection": "downloads", "status": "x"}, headers=admin_headers)
    assert res.status_code == 500
    assert res.json()["detail"] == "Failed to fetch data."


def test_dashboard_counts_recent_and_risk_split(
    client: TestClient, admin_headers: dict[str, str], insert_row: Callable[..., dict[str, Any]]
) -> None:
    _lead(insert_row, "A", "contact")
    for risk in ("green", "amber", "amber", "red"):
        insert_row("app.assessments", email=f"{risk}@x.test", industry="it", risk_level=risk)

    res = client.get("/api/v1/admin/dashboard", headers=admin_headers)

    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["leads"] == 1
    assert body["counts"]["assessments"] == 4
    assert body["risk"] == {"green": 1, "amber": 2, "red": 1, "total": 4}
    assert len(body["recent"]["assessments"]) == 4
    assert body["recent"]["leads"][0]["name"] == "A"
    assert set(body["recent"]) == {
        "leads",
        "subscribers",
        "downloads",
        "survey_responses",
        "assessments",
        "briefings",
    }


def test_dashboard_is_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/admin/dashboard", headers=blogger_headers).status_code == 403
