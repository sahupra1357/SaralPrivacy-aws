"""Pure SEO watcher logic — ported from tools/seo/verdict.test.ts, case for case."""

from datetime import UTC, datetime
from typing import Any, cast

from app.api.routes import seo

NOW = datetime(2026, 9, 6, tzinfo=UTC)  # 5+ weeks after the 31 Jul / 1 Aug requests
B = seo.BASE

CTX: dict[str, Any] = {
    "watchlist": set(seo.WATCHLIST),
    "sitemap": {
        *seo.WATCHLIST,
        f"{B}/learn/consent",
        f"{B}/blog/some-post",
        f"{B}/briefings/a",
        f"{B}/briefings/b",
    },
    "ledger": seo.REQUESTED_INDEXING,
}


def rec(url: str, bucket: str, **extra: Any) -> dict[str, Any]:
    base = seo.to_record(url, None, **CTX)
    return {**base, "bucket": bucket, **extra}


def watchlist_as(bucket: str, crawled: bool) -> list[dict[str, Any]]:
    return [
        rec(u, bucket, last_crawl_time="2026-09-01T00:00:00Z" if crawled else None)
        for u in seo.WATCHLIST
    ]


def test_watchlist_has_17_paths_all_in_the_ledger() -> None:
    assert len(seo.WATCHLIST_PATHS) == 17
    assert all(p in seo.REQUESTED_INDEXING for p in seo.WATCHLIST_PATHS)
    assert seo.WATCHLIST[0] == "https://saralprivacy.com/discovery"


def test_bucket_of_maps_gsc_coverage_states() -> None:
    assert seo.bucket_of({"verdict": "PASS", "coverageState": "Submitted and indexed"}) == "indexed"
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "Indexed, not submitted in sitemap"})
        == "indexed"
    )
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "Discovered - currently not indexed"})
        == "discovered"
    )
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "Crawled - currently not indexed"})
        == "crawled_not_indexed"
    )
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "URL is unknown to Google"})
        == "unknown"
    )
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "Excluded by 'noindex' tag"})
        == "excluded"
    )
    assert (
        seo.bucket_of({"verdict": "NEUTRAL", "coverageState": "Page with redirect"}) == "excluded"
    )
    assert seo.bucket_of({"verdict": "FAIL", "coverageState": "Soft 404"}) == "other"
    assert seo.bucket_of(None) == "error"


def test_normalize_crawl_time_treats_the_epoch_as_never_crawled() -> None:
    assert seo.normalize_crawl_time("1970-01-01T00:00:00Z") is None
    assert seo.normalize_crawl_time(None) is None
    assert seo.normalize_crawl_time("2026-08-20T10:00:00Z") == "2026-08-20T10:00:00Z"


def test_to_record_flags_ledger_and_epoch() -> None:
    r = seo.to_record(
        f"{B}/discovery",
        {
            "indexStatusResult": {
                "verdict": "NEUTRAL",
                "coverageState": "Discovered - currently not indexed",
                "lastCrawlTime": "1970-01-01T00:00:00Z",
                "referringUrls": ["a", "b"],
            }
        },
        **CTX,
    )
    assert r["watchlist"] is True and r["in_sitemap"] is True
    assert r["bucket"] == "discovered"
    assert r["last_crawl_time"] is None
    assert r["requested_indexing_at"] == "2026-07-31"
    assert r["referring_urls"] == 2
    e = seo.to_record(f"{B}/discovery", None, **CTX, error="HTTP 403")
    assert e["bucket"] == "error" and e["error"] == "HTTP 403"


def test_path_of() -> None:
    assert seo.path_of(f"{B}/a/b?x=1") == "/a/b"
    assert seo.path_of(B) == "/"
    assert seo.path_of("not a url") == "not a url"


def test_decide_starved_when_nothing_crawled_after_5_weeks() -> None:
    v = seo.decide(watchlist_as("discovered", False), now=NOW, prev_discovered=None)
    assert v["code"] == "STARVED"
    assert v["evidence"]["crawled"] == 0
    assert v["evidence"]["weeks_since_request"] >= 5
    assert "D-D" in v["next"]


def test_decide_queue_moved_when_a_majority_carry_crawl_dates() -> None:
    recs = watchlist_as("discovered", False)
    for i in range(9):
        recs[i] = {
            **recs[i],
            "bucket": "indexed" if i < 4 else "crawled_not_indexed",
            "last_crawl_time": "2026-08-25T00:00:00Z",
        }
    v = seo.decide(recs, now=NOW, prev_discovered=None)
    assert v["code"] == "QUEUE_MOVED"
    assert v["evidence"]["crawled"] == 9 and v["evidence"]["indexed"] == 4
    assert "B3" in v["next"]


def test_decide_queue_moved_when_discovered_shrank_30_percent() -> None:
    recs = watchlist_as("discovered", False)
    for i in range(6):
        recs[i] = {
            **recs[i],
            "bucket": "crawled_not_indexed",
            "last_crawl_time": "2026-08-25T00:00:00Z",
        }
    assert seo.decide(recs, now=NOW, prev_discovered=17)["code"] == "QUEUE_MOVED"
    assert seo.decide(recs, now=NOW, prev_discovered=None)["code"] == "STARVED"


def test_decide_too_early_under_5_weeks() -> None:
    v = seo.decide(
        watchlist_as("discovered", False),
        now=datetime(2026, 8, 20, tzinfo=UTC),
        prev_discovered=None,
    )
    assert v["code"] == "TOO_EARLY"
    assert v["summary"].endswith("crawled — under the 5-week threshold.")


def test_decide_insufficient_data_when_most_inspections_failed() -> None:
    recs = [
        {**r, "bucket": "error", "error": "HTTP 403"} if i < 10 else r
        for i, r in enumerate(watchlist_as("discovered", False))
    ]
    v = seo.decide(recs, now=NOW, prev_discovered=None)
    assert v["code"] == "INSUFFICIENT_DATA"
    assert v["summary"] == "Only 7/17 watchlist inspections succeeded."


def test_diff_runs_reports_crawl_index_and_regression_movement() -> None:
    prev = [
        {"url": f"{B}/discovery", "bucket": "discovered", "last_crawl_time": None},
        {"url": f"{B}/rights", "bucket": "indexed", "last_crawl_time": "2026-08-01T00:00:00Z"},
        {"url": f"{B}/blog", "bucket": "discovered", "last_crawl_time": None},
    ]
    curr = [
        rec(f"{B}/discovery", "indexed", last_crawl_time="2026-09-01T00:00:00Z"),
        rec(f"{B}/rights", "crawled_not_indexed", last_crawl_time="2026-09-01T00:00:00Z"),
        rec(f"{B}/blog", "discovered"),
        rec(f"{B}/new-page", "indexed"),
    ]
    d = seo.diff_runs(cast(list[dict[str, Any]], prev), "2026-08-30T00:00:00Z", curr)
    assert d is not None
    assert d["compared"] == 3
    assert d["newly_crawled"] == [f"{B}/discovery"]
    assert d["newly_indexed"] == [f"{B}/discovery"]
    assert d["regressed"] == [f"{B}/rights"]
    assert len(d["changed"]) == 2
    assert seo.diff_runs(None, None, curr) is None


def test_crawled_not_indexed_breakdown_confirms_the_briefings_hypothesis() -> None:
    recs = [
        *[rec(f"{B}/briefings/b{i}", "crawled_not_indexed") for i in range(8)],
        rec(f"{B}/blog/x", "crawled_not_indexed"),
        rec(f"{B}/learn/consent", "crawled_not_indexed"),
        rec(f"{B}/discovery", "discovered"),
    ]
    b = seo.crawled_not_indexed_breakdown(recs)
    assert b["total"] == 10
    assert b["by"] == {"briefings": 8, "blog": 1, "other": 1}
    assert b["hypothesis"] == "confirmed"
    assert seo.crawled_not_indexed_breakdown(recs[:3])["hypothesis"] == "insufficient"


def test_shortlist_commercial_first_never_ledger_sitemap_only() -> None:
    recs = [
        *watchlist_as("discovered", False),
        rec(f"{B}/learn/consent", "discovered"),
        rec(f"{B}/blog/some-post", "crawled_not_indexed"),
        rec(f"{B}/briefings/a", "discovered"),
        rec(f"{B}/briefings/b", "indexed"),
        rec(f"{B}/not-in-sitemap", "discovered"),
        *[rec(f"{B}/briefings/z{i}", "discovered", in_sitemap=True) for i in range(12)],
    ]
    s = seo.shortlist(recs)
    assert len(s) == 10
    assert s[0]["url"] == f"{B}/learn/consent"
    assert s[0]["reason"] == "discovered · commercial"
    assert s[1]["url"] == f"{B}/blog/some-post"
    assert all(x["url"] not in seo.WATCHLIST for x in s)
    assert all(x["url"] not in (f"{B}/briefings/b", f"{B}/not-in-sitemap") for x in s)


def sanity_pair(
    n: int, *, regress: int, recrawl: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    urls = seo.WATCHLIST[:n]
    # Postgres hands back +00:00, GSC answers Z — the same instant must compare equal.
    prev = [
        {"url": u, "bucket": "indexed", "last_crawl_time": "2026-08-01T00:00:00+00:00"}
        for u in urls
    ]
    curr = [
        rec(
            u,
            "crawled_not_indexed" if i < regress else "indexed",
            last_crawl_time="2026-09-08T00:00:00Z" if i < recrawl else "2026-08-01T00:00:00Z",
        )
        for i, u in enumerate(urls)
    ]
    return prev, curr


def test_data_sanity_mass_regression_without_recrawl_is_suspect() -> None:
    prev, curr = sanity_pair(16, regress=16, recrawl=0)
    s = seo.check_data_sanity(prev, curr)
    assert s["suspect"] is True
    assert (s["regressed"], s["prev_indexed"], s["stale_crawl"]) == (16, 16, 16)


def test_data_sanity_mass_regression_with_recrawls_is_believed() -> None:
    prev, curr = sanity_pair(16, regress=16, recrawl=16)
    assert seo.check_data_sanity(prev, curr)["suspect"] is False


def test_data_sanity_a_few_stale_regressions_are_churn() -> None:
    prev, curr = sanity_pair(16, regress=4, recrawl=0)
    s = seo.check_data_sanity(prev, curr)
    assert s["suspect"] is False
    assert s["reason"] == "4/16 regressed (4 without a recrawl) — within normal churn"


def test_data_sanity_no_previous_run_is_never_suspect() -> None:
    _prev, curr = sanity_pair(16, regress=16, recrawl=0)
    assert seo.check_data_sanity(None, curr)["suspect"] is False


def test_suspect_verdict_withholds_the_real_verdict() -> None:
    prev, curr = sanity_pair(16, regress=16, recrawl=0)
    s = seo.check_data_sanity(prev, curr)
    real = seo.decide(curr, now=NOW, prev_discovered=None)
    v = seo.suspect_verdict(s, real["evidence"])
    assert v["code"] == "SUSPECT_DATA"
    assert "do not treat it as the new baseline" in v["next"].lower()
    assert v["evidence"] == real["evidence"]


def test_merge_ledger_db_wins() -> None:
    assert seo.merge_ledger({"/a": "2026-07-31", "/b": "x"}, {"/a": "2026-09-01"}) == {
        "/a": "2026-09-01",
        "/b": "x",
    }
