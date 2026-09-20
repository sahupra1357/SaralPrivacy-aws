"""SEO watcher I/O: GSC client, sitemap, the run, persistence and the /admin/seo routes."""

import json
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import seo
from app.core.config import settings
from app.models.admin import SeoIndexRequest, SeoInspection, SeoRun

B = seo.BASE
NOW = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)
SA_JSON = json.dumps(
    {
        "type": "service_account",
        "client_email": "bot@proj.iam.gserviceaccount.com",
        "private_key": "-----KEY-----",
    }
)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(seo, "_sleep", slept.append)
    return slept


class FakeGsc:
    """Fixture client: URL → inspection result, like createDryRunClient."""

    def __init__(
        self,
        inspections: dict[str, dict[str, Any]] | None = None,
        default: dict[str, Any] | None = None,
    ) -> None:
        self.inspections = inspections or {}
        self.default = (
            default
            if default is not None
            else {
                "indexStatusResult": {
                    "verdict": "NEUTRAL",
                    "coverageState": "Discovered - currently not indexed",
                }
            }
        )
        self.submitted: list[str] = []
        self.fail: set[str] = set()

    def inspect(self, url: str) -> dict[str, Any]:
        if url in self.fail:
            raise RuntimeError("GSC HTTP 500: boom")
        return self.inspections.get(url, self.default)

    def search_analytics(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        self.window = (start_date, end_date)
        return [
            {
                "keys": [f"{B}/discovery"],
                "clicks": 3,
                "impressions": 120,
                "ctr": 0.02,
                "position": 11.26,
            }
        ]

    def list_sitemaps(self) -> list[dict[str, Any]]:
        return [{"path": f"{B}/sitemap.xml"}]

    def submit_sitemap(self, feedpath: str) -> None:
        self.submitted.append(feedpath)


# ── Service account ───────────────────────────────────────────────────────
def test_parse_service_account_validates_the_key() -> None:
    sa = seo.parse_service_account(SA_JSON, "GSC_SERVICE_ACCOUNT_JSON")
    assert sa["client_email"] == "bot@proj.iam.gserviceaccount.com"
    assert sa["token_uri"] == "https://oauth2.googleapis.com/token"
    with pytest.raises(ValueError, match="GSC_SERVICE_ACCOUNT_JSON: not valid JSON"):
        seo.parse_service_account("{", "GSC_SERVICE_ACCOUNT_JSON")
    with pytest.raises(
        ValueError, match=r"expected a Google service-account key \(type=service_account"
    ):
        seo.parse_service_account(json.dumps({"type": "user"}), "GSC_SERVICE_ACCOUNT_JSON")


def test_load_service_account_reads_settings_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", "")
    assert seo.load_service_account() is None
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", f"  {SA_JSON}  ")
    loaded = seo.load_service_account()
    assert loaded is not None and loaded[1] == "env:GSC_SERVICE_ACCOUNT_JSON"


# ── GSC client ────────────────────────────────────────────────────────────
def _client(handler: Any) -> seo.GscClient:
    return seo.GscClient(
        {"client_email": "x", "private_key": "y"},
        seo.SITE,
        token_fetcher=lambda _sa: "tok",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_gsc_inspect_posts_to_the_url_inspection_api() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"inspectionResult": {"indexStatusResult": {"verdict": "PASS"}}}
        )

    result = _client(handler).inspect(f"{B}/rights")

    assert result == {"indexStatusResult": {"verdict": "PASS"}}
    req = seen[0]
    assert str(req.url) == "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
    assert req.headers["authorization"] == "Bearer tok"
    assert json.loads(req.content) == {
        "inspectionUrl": f"{B}/rights",
        "siteUrl": seo.SITE,
        "languageCode": "en-US",
    }


def test_gsc_retries_429_and_5xx_then_succeeds(no_sleep: list[float]) -> None:
    codes = iter([429, 503, 200])

    def handler(_r: httpx.Request) -> httpx.Response:
        code = next(codes)
        return httpx.Response(code, json={"sitemap": [{"path": "s"}]} if code == 200 else {})

    assert _client(handler).list_sitemaps() == [{"path": "s"}]
    assert no_sleep == [0.5, 1.0]


def test_gsc_gives_up_with_a_hinted_error() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(seo.GscError) as err:
        _client(handler).search_analytics("2026-08-01", "2026-08-28")
    assert str(err.value).startswith(
        "GSC HTTP 403: forbidden — service account is not a user on the property"
    )


def test_gsc_submit_sitemap_encodes_both_site_and_feed() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    _client(handler).submit_sitemap(f"{B}/sitemap.xml")
    assert seen[0].method == "PUT"
    assert (
        seen[0]
        .url.raw_path.decode()
        .endswith(
            "/sites/https%3A%2F%2Fsaralprivacy.com%2F/sitemaps/https%3A%2F%2Fsaralprivacy.com%2Fsitemap.xml"
        )
    )


# ── Sitemap ───────────────────────────────────────────────────────────────
def test_fetch_sitemap_follows_a_sitemap_index(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        f"{B}/sitemap.xml": "<sitemapindex><sitemap><loc>https://saralprivacy.com/s1.xml</loc></sitemap></sitemapindex>",
        f"{B}/s1.xml": "<urlset><url><loc> https://saralprivacy.com/a </loc></url><url><loc>https://saralprivacy.com/b</loc></url></urlset>",
    }
    monkeypatch.setattr(
        seo,
        "_http_get",
        lambda url: httpx.Response(200, text=pages[url], request=httpx.Request("GET", url)),
    )
    assert seo.fetch_sitemap_urls(f"{B}/sitemap.xml") == [f"{B}/a", f"{B}/b"]


def test_resolve_sitemap_falls_back_to_the_previous_run(
    monkeypatch: pytest.MonkeyPatch, no_sleep: list[float]
) -> None:
    monkeypatch.setattr(
        seo, "_http_get", lambda url: httpx.Response(500, request=httpx.Request("GET", url))
    )
    lines: list[str] = []
    urls, fetched = seo.resolve_sitemap(
        seo.SITE, {"sitemap_urls": [f"{B}/old"]}, None, lines.append
    )
    assert (urls, fetched) == ([f"{B}/old"], False)
    assert no_sleep == [1.5, 3.0, 4.5]
    assert lines == [
        "sitemap fetch failed after retries (https://saralprivacy.com/sitemap.xml: HTTP 500); using the previous run's sitemap"
    ]
    assert seo.resolve_sitemap(seo.SITE, None, None, lines.append) == ([], False)
    assert lines[-1].endswith("using the watchlist only")


# ── The run ───────────────────────────────────────────────────────────────
def _run(api: FakeGsc, **kw: Any) -> tuple[dict[str, Any], list[str]]:
    lines: list[str] = []
    opts: dict[str, Any] = {
        "api": api,
        "site": seo.SITE,
        "scope": "newcomers",
        "budget": seo.DEFAULT_BUDGET,
        "analytics": True,
        "submit_sitemap": False,
        "prev": None,
        "ledger": dict(seo.REQUESTED_INDEXING),
        "sitemap_urls": [*seo.WATCHLIST, f"{B}/learn/consent", f"{B}/blog/x"],
        "sitemap_fetched": True,
        "key_source": "env:GSC_SERVICE_ACCOUNT_JSON",
        "now": NOW,
        "log_line": lines.append,
    }
    opts.update(kw)
    return seo.run_inspection(**opts), lines


def test_run_inspects_watchlist_plus_newcomers_and_enriches_analytics() -> None:
    api = FakeGsc()
    api.fail.add(f"{B}/blog/x")
    report, lines = _run(api)

    assert report["inspected"] == 19
    assert report["errors"] == 1
    assert report["sitemap"]["newcomers"] == [f"{B}/learn/consent", f"{B}/blog/x"]
    assert report["run_at"] == "2026-09-14T04:00:00.000Z"
    discovery = next(r for r in report["urls"] if r["path"] == "/discovery")
    assert discovery["search_28d"] == {"clicks": 3, "impressions": 120, "position": 11.3}
    assert api.window == ("2026-08-15", "2026-09-11")
    assert report["verdict"]["code"] == "STARVED"
    assert [s["url"] for s in report["shortlist"]] == [f"{B}/learn/consent"]
    assert lines[0] == "sitemap: 19 URLs · 2 newcomers"
    assert f"  ✗ {B}/blog/x: GSC HTTP 500: boom" in lines
    assert "inspected 19 (1 errors)" in lines
    assert report["sitemaps_in_gsc"] == [{"path": f"{B}/sitemap.xml"}]


def test_run_watchlist_scope_and_budget_never_cut_the_watchlist() -> None:
    report, lines = _run(FakeGsc(), scope="full", budget=5)
    assert report["inspected"] == 17
    assert "budget 5: inspecting 17 of 19 candidate URLs" in lines
    only, _ = _run(FakeGsc(), scope="watchlist")
    assert only["inspected"] == 17


def test_run_resubmits_the_sitemap_when_asked() -> None:
    api = FakeGsc()
    report, lines = _run(api, submit_sitemap=True)
    assert api.submitted == ["https://saralprivacy.com/sitemap.xml"]
    assert report["sitemap_submitted"] is True
    assert "sitemap resubmitted: https://saralprivacy.com/sitemap.xml" in lines


def test_run_flags_a_suspect_mass_regression() -> None:
    prev = {
        "run_at": "2026-09-07T04:00:00+00:00",
        "sitemap_urls": list(seo.WATCHLIST),
        "records": [
            {"url": u, "bucket": "indexed", "last_crawl_time": "2026-08-01T00:00:00+00:00"}
            for u in seo.WATCHLIST
        ],
    }
    api = FakeGsc(
        default={
            "indexStatusResult": {
                "verdict": "NEUTRAL",
                "coverageState": "Crawled - currently not indexed",
                "lastCrawlTime": "2026-08-01T00:00:00Z",
            }
        }
    )
    report, lines = _run(api, prev=prev, sitemap_urls=list(seo.WATCHLIST))
    assert report["data_sanity"]["suspect"] is True
    assert report["verdict"]["code"] == "SUSPECT_DATA"
    assert any(line.startswith("⚠ SUSPECT DATA — 17/17 previously-indexed") for line in lines)


# ── execute_inspection: persistence ───────────────────────────────────────
def _patch_io(monkeypatch: pytest.MonkeyPatch, api: FakeGsc, sitemap: list[str]) -> None:
    monkeypatch.setattr(seo, "make_client", lambda _sa: api)
    xml = "<urlset>" + "".join(f"<url><loc>{u}</loc></url>" for u in sitemap) + "</urlset>"
    monkeypatch.setattr(
        seo,
        "_http_get",
        lambda url: httpx.Response(200, text=xml, request=httpx.Request("GET", url)),
    )


def test_execute_inspection_persists_the_run_and_uses_the_db_ledger(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(SeoIndexRequest(url=f"{B}/learn/consent", requested_at=date(2026, 9, 1), note="n"))
    session.commit()
    _patch_io(monkeypatch, FakeGsc(), [*seo.WATCHLIST, f"{B}/learn/consent", f"{B}/blog/x"])

    ok, payload = seo.execute_inspection(
        session,
        sa={},
        key_source="env:GSC_SERVICE_ACCOUNT_JSON",
        scope="newcomers",
        submit_sitemap=False,
        now=NOW,
    )

    assert ok is True
    assert payload["ok"] is True and payload["persisted"] is True
    assert payload["verdict"] == "STARVED"
    assert payload["inspected"] == 19 and payload["newcomers"] == 2
    assert payload["shortlist"] == 1  # /learn/consent is in the DB ledger now
    assert isinstance(payload["log"], list) and payload["log"]
    run = session.exec(select(SeoRun)).one()
    assert str(run.id) == payload["run_id"]
    assert run.verdict_code == "STARVED" and run.scope == "newcomers"
    assert run.summary is not None and run.summary["verdict"]["code"] == "STARVED"
    rows = session.exec(select(SeoInspection)).all()
    assert len(rows) == 19
    consent = next(r for r in rows if r.path == "/learn/consent")
    assert consent.requested_indexing_at == date(2026, 9, 1)


def test_execute_inspection_diffs_against_the_stored_run_and_skips_suspect_runs(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    indexed = FakeGsc(
        default={
            "indexStatusResult": {
                "verdict": "PASS",
                "coverageState": "Submitted and indexed",
                "lastCrawlTime": "2026-08-01T00:00:00Z",
            }
        }
    )
    _patch_io(monkeypatch, indexed, list(seo.WATCHLIST))
    seo.execute_inspection(
        session, sa={}, key_source="k", scope="watchlist", submit_sitemap=False, now=NOW
    )

    flipped = FakeGsc(
        default={
            "indexStatusResult": {
                "verdict": "NEUTRAL",
                "coverageState": "Crawled - currently not indexed",
                "lastCrawlTime": "2026-08-01T00:00:00Z",
            }
        }
    )
    _patch_io(monkeypatch, flipped, list(seo.WATCHLIST))
    ok, payload = seo.execute_inspection(
        session, sa={}, key_source="k", scope="watchlist", submit_sitemap=False, now=NOW
    )

    assert ok is True
    assert payload["persisted"] is False
    assert payload["verdict"] == "SUSPECT_DATA"
    assert payload["log"][-1].startswith(
        "NOT persisted — suspect data (17/17 previously-indexed URLs regressed"
    )
    assert len(session.exec(select(SeoRun)).all()) == 1


def test_execute_inspection_reports_failures(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_s: Session) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(seo, "fetch_prev_run", boom)
    ok, payload = seo.execute_inspection(
        session, sa={}, key_source="k", scope="full", submit_sitemap=False
    )
    assert ok is False
    assert payload == {
        "ok": False,
        "error": "db down",
        "durationMs": payload["durationMs"],
        "log": [],
    }


# ── Routes ────────────────────────────────────────────────────────────────
def test_inspect_route_needs_the_service_account(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", "")
    res = client.post("/api/v1/admin/seo-inspect", json={}, headers=admin_headers)
    assert res.status_code == 500
    assert res.json()["detail"] == "GSC_SERVICE_ACCOUNT_JSON is not configured in the Vercel env"

    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", "{nope")
    bad = client.post("/api/v1/admin/seo-inspect", json={}, headers=admin_headers)
    assert bad.status_code == 500
    assert bad.json()["detail"] == "GSC_SERVICE_ACCOUNT_JSON: not valid JSON"


def test_inspect_route_runs_in_the_background(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", SA_JSON)
    captured: dict[str, Any] = {}

    def fake_execute(_s: Session, **kw: Any) -> tuple[bool, dict[str, Any]]:
        captured.update(kw)
        return True, {"ok": True, "verdict": "TOO_EARLY", "summary": "s", "log": []}

    monkeypatch.setattr(seo, "execute_inspection", fake_execute)
    res = client.post(
        "/api/v1/admin/seo-inspect",
        json={"scope": "bogus", "submitSitemap": True},
        headers=admin_headers,
    )

    assert res.status_code == 202
    assert captured["scope"] == "newcomers"
    assert captured["submit_sitemap"] is True
    assert captured["key_source"] == "env:GSC_SERVICE_ACCOUNT_JSON"
    task = client.get(f"/api/v1/admin/tasks/{res.json()['task_id']}", headers=admin_headers).json()
    assert task["kind"] == "seo-inspect" and task["status"] == "done"
    assert task["result"]["verdict"] == "TOO_EARLY"


def test_inspect_route_marks_a_failed_run(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "GSC_SERVICE_ACCOUNT_JSON", SA_JSON)
    monkeypatch.setattr(
        seo, "execute_inspection", lambda _s, **_k: (False, {"ok": False, "error": "quota"})
    )
    res = client.post("/api/v1/admin/seo-inspect", headers=admin_headers)
    task = client.get(f"/api/v1/admin/tasks/{res.json()['task_id']}", headers=admin_headers).json()
    assert task["status"] == "failed" and task["result"]["error"] == "quota"


def test_read_seo_empty_then_ready(
    client: TestClient,
    session: Session,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty = client.get("/api/v1/admin/seo", headers=admin_headers).json()
    assert empty == {"state": "empty", "ledger": []}

    _patch_io(monkeypatch, FakeGsc(), list(seo.WATCHLIST))
    seo.execute_inspection(
        session, sa={}, key_source="k", scope="watchlist", submit_sitemap=False, now=NOW
    )
    body = client.get("/api/v1/admin/seo", headers=admin_headers).json()

    assert body["state"] == "ready"
    assert body["latest"] == body["runs"][0]
    assert set(body["latest"]) == set(seo.RUN_FIELDS)
    assert body["latest"]["verdict_code"] == "STARVED"
    assert len(body["rows"]) == 17
    assert set(body["rows"][0]) == set(seo.INSPECTION_FIELDS)


def test_mark_requested_upserts_the_ledger(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    url = f"{B}/learn/consent"
    first = client.post(
        "/api/v1/admin/seo/index-requests", json={"url": f" {url} "}, headers=admin_headers
    )
    again = client.post(
        "/api/v1/admin/seo/index-requests", json={"url": url}, headers=admin_headers
    )

    assert first.status_code == 200 and again.status_code == 200
    rows = session.exec(select(SeoIndexRequest)).all()
    assert len(rows) == 1
    assert rows[0].note == "admin: Request Indexing pressed"
    ledger = client.get("/api/v1/admin/seo", headers=admin_headers).json()["ledger"]
    assert ledger[0]["url"] == url


def test_mark_requested_rejects_other_hosts(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.post(
        "/api/v1/admin/seo/index-requests",
        json={"url": "https://evil.example/"},
        headers=admin_headers,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "URL must be on https://saralprivacy.com/"


def test_seo_routes_are_admin_only(client: TestClient, blogger_headers: dict[str, str]) -> None:
    assert client.get("/api/v1/admin/seo", headers=blogger_headers).status_code == 403
    assert client.post("/api/v1/admin/seo-inspect", headers=blogger_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/seo/index-requests", json={}, headers=blogger_headers
        ).status_code
        == 403
    )
