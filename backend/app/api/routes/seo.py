"""SEO observability agent: the Google Search Console watcher.

Watchlist (17 never-crawled commercial pages) + sitemap newcomers
  → URL Inspection API per URL (last crawl / coverage / canonical)
  → ops.seo_runs / ops.seo_inspections
  → diff vs the previous run
  → the pre-agreed verdict (QUEUE_MOVED / STARVED / TOO_EARLY)
  → a ≤10-URL human shortlist for the "Request Indexing" button.

Rewrites lib/seo/{watchlist,verdict,gsc,run,db}.ts and /api/admin/seo-inspect. The weekly
run (was .github/workflows/seo-inspect.yml) is the `seo-inspect` job in app/jobs/admin.py.

Boundary (settled): URL Inspection, Search Analytics and Sitemaps are automated here.
"Request Indexing" has no API for ordinary pages and the GSC UI is never puppeted — the
watcher only emits the shortlist. Quotas: 2,000 inspections/day and 600/min per property.
"""

import json
import logging
import math
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from app.api.deps import SessionDep
from app.api.routes.admin import AdminUser, iso_z, start_task
from app.core.config import settings
from app.crud import admin as crud
from app.models.admin import SeoInspection, SeoRun

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["seo"])

Record = dict[str, Any]
LogFn = Callable[[str], None]

# ── Watchlist and request-indexing ledger (lib/seo/watchlist.ts) ────────────
# The 17 core commercial pages the 2026-07-31 GSC baseline found NEVER crawled —
# crawl-budget starvation, not a technical defect. Re-requesting a URL does not jump the
# queue, so a URL in the ledger is never shortlisted again.

SITE = "https://saralprivacy.com/"  # URL-prefix property — always inspect here
BASE = "https://saralprivacy.com"

WATCHLIST_PATHS: tuple[str, ...] = (
    "/discovery",
    "/tools/dpdpa-privacy-notice-generator",
    "/data-mapping",
    "/blog",
    "/rights",
    "/media/coverage",
    "/media/press-wall",
    "/industries/ca-firms",
    "/industries/d2c-brands",
    "/industries/clinics-diagnostic-labs",
    "/industries/schools-colleges",
    "/industries/law-firms",
    "/industries/real-estate",
    "/industries/pharmacies",
    "/industries/hotels-travel",
    "/industries/gyms-salons-spas",
    # The handoff calls this "recruitment-staffing"; the live slug is recruitment-agencies.
    "/industries/recruitment-agencies/data-flow",
)

WATCHLIST: tuple[str, ...] = tuple(BASE + p for p in WATCHLIST_PATHS)

REQUESTED_INDEXING: dict[str, str] = {
    "/discovery": "2026-07-31",
    "/tools/dpdpa-privacy-notice-generator": "2026-07-31",
    "/data-mapping": "2026-07-31",
    "/blog": "2026-07-31",
    "/industries/ca-firms": "2026-07-31",
    "/industries/d2c-brands": "2026-07-31",
    "/industries/clinics-diagnostic-labs": "2026-07-31",
    "/industries/schools-colleges": "2026-07-31",
    "/industries/law-firms": "2026-08-01",
    "/industries/real-estate": "2026-08-01",
    "/industries/pharmacies": "2026-08-01",
    "/industries/hotels-travel": "2026-08-01",
    "/industries/gyms-salons-spas": "2026-08-01",
    "/industries/recruitment-agencies/data-flow": "2026-08-01",
    "/rights": "2026-08-01",
    "/media/coverage": "2026-08-01",
    "/media/press-wall": "2026-08-01",
}

# ── Decision-tree thresholds (lib/seo/verdict.ts) ───────────────────────────
BUCKETS: tuple[str, ...] = (
    "indexed",
    "discovered",
    "crawled_not_indexed",
    "unknown",
    "excluded",
    "other",
    "error",
)
MOVED_CRAWLED_SHARE = 0.5  # ≥ this share of the watchlist with a real crawl date = moved
SHRINK_RATIO = 0.7  # discovered ≤ this ratio of the previous run (with crawl movement) = moved
STARVED_WEEKS = 5  # still no crawl dates after this many weeks since the request = starved
SHORTLIST_MAX = 10  # GSC button quota ≈ 10/day
SUSPECT_MIN_REGRESSIONS = 5
SUSPECT_REGRESSED_SHARE = 0.5
SUSPECT_STALE_CRAWL_SHARE = 0.9

SCOPES: tuple[str, ...] = ("watchlist", "newcomers", "full")
DEFAULT_BUDGET = 500  # ¼ of the 2,000/day URL Inspection quota; the watchlist is never cut
CONCURRENCY = 3
PERSIST_PAGE = 500

GSC_NOT_CONFIGURED = "GSC_SERVICE_ACCOUNT_JSON is not configured in the Vercel env"
URL_NOT_ON_SITE = "URL must be on https://saralprivacy.com/"
INDEX_REQUEST_NOTE = "admin: Request Indexing pressed"


# ── Small helpers ───────────────────────────────────────────────────────────
def _sleep(seconds: float) -> None:
    """Every back-off goes through here so tests never wait."""
    time.sleep(seconds)


def _round(value: float, places: int) -> float:
    """`Number(x.toFixed(n))`."""
    return float(f"{value:.{places}f}")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _same_time(a: str | None, b: str | None) -> bool:
    """Crawl timestamps compared as instants: GSC answers `…Z`, Postgres `…+00:00`."""
    if a is None or b is None:
        return a is b
    pa, pb = _parse_time(a), _parse_time(b)
    if pa is None or pb is None:
        return a == b
    return pa == pb


def iso_day(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d")


# ── Per-URL classification ──────────────────────────────────────────────────
_EPOCH = re.compile(r"^1970-01-01T")


def normalize_crawl_time(value: str | None) -> str | None:
    """The API omits lastCrawlTime for never-crawled URLs; GSC exports show the epoch."""
    if not value or _EPOCH.match(value):
        return None
    return value


def bucket_of(s: dict[str, Any] | None) -> str:
    if not s:
        return "error"
    cov = str(s.get("coverageState") or "").lower()
    if s.get("verdict") == "PASS":
        return "indexed"
    if "indexed" in cov and "not indexed" not in cov:
        return "indexed"
    if cov.startswith("discovered"):
        return "discovered"
    if cov.startswith("crawled"):
        return "crawled_not_indexed"
    if "unknown to google" in cov:
        return "unknown"
    if any(k in cov for k in ("noindex", "redirect", "canonical", "duplicate", "blocked")):
        return "excluded"
    return "other"


def path_of(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if not parsed.scheme or not parsed.netloc:
        return url
    return parsed.path or "/"


def to_record(
    url: str,
    result: dict[str, Any] | None,
    *,
    watchlist: set[str],
    sitemap: set[str],
    ledger: dict[str, str],
    error: str | None = None,
) -> Record:
    s = (result or {}).get("indexStatusResult") if result is not None else None
    path = path_of(url)
    return {
        "url": url,
        "path": path,
        "watchlist": url in watchlist,
        "in_sitemap": url in sitemap,
        "bucket": "error" if error else bucket_of(s),
        "coverage_state": (s or {}).get("coverageState"),
        "verdict": (s or {}).get("verdict"),
        "indexing_state": (s or {}).get("indexingState"),
        "last_crawl_time": normalize_crawl_time((s or {}).get("lastCrawlTime")),
        "google_canonical": (s or {}).get("googleCanonical"),
        "user_canonical": (s or {}).get("userCanonical"),
        "robots_txt_state": (s or {}).get("robotsTxtState"),
        "page_fetch_state": (s or {}).get("pageFetchState"),
        "referring_urls": len((s or {}).get("referringUrls") or []),
        "requested_indexing_at": ledger.get(path),
        "search_28d": None,
        "error": error,
        "raw": s,
    }


def bucket_counts(records: list[Record]) -> dict[str, int]:
    out = dict.fromkeys(BUCKETS, 0)
    for r in records:
        out[r["bucket"]] += 1
    return out


# ── The decision tree ───────────────────────────────────────────────────────
def weeks_between(start: datetime, end: datetime) -> int:
    return math.floor((end - start).total_seconds() / (7 * 86_400))


def decide(records: list[Record], *, now: datetime, prev_discovered: int | None) -> dict[str, Any]:
    watch = [r for r in records if r["watchlist"]]
    inspected = [r for r in watch if r["bucket"] != "error"]
    crawled = [r for r in inspected if r["last_crawl_time"] is not None]
    indexed = [r for r in inspected if r["bucket"] == "indexed"]
    discovered = [r for r in inspected if r["bucket"] in ("discovered", "unknown")]
    crawled_share = len(crawled) / len(inspected) if inspected else 0

    request_dates = sorted(r["requested_indexing_at"] for r in watch if r["requested_indexing_at"])
    earliest = (
        datetime.fromisoformat(request_dates[0]).replace(tzinfo=UTC) if request_dates else None
    )
    weeks = weeks_between(earliest, now) if earliest else None

    evidence = {
        "watchlist": len(watch),
        "inspected": len(inspected),
        "crawled": len(crawled),
        "indexed": len(indexed),
        "discovered": len(discovered),
        "crawled_share": _round(crawled_share, 2),
        "weeks_since_request": weeks,
        "prev_discovered": prev_discovered,
    }

    if len(watch) == 0 or len(inspected) < math.ceil(len(watch) / 2):
        return {
            "code": "INSUFFICIENT_DATA",
            "summary": f"Only {len(inspected)}/{len(watch)} watchlist inspections succeeded.",
            "next": "Check the service-account key, property access and quota, then re-run. No decision can be read from this run.",
            "evidence": evidence,
        }

    shrank = (
        prev_discovered is not None
        and prev_discovered > 0
        and len(discovered) <= prev_discovered * SHRINK_RATIO
        and len(crawled) > 0
    )

    if crawled_share >= MOVED_CRAWLED_SHARE or shrank:
        return {
            "code": "QUEUE_MOVED",
            "summary": f"{len(crawled)}/{len(inspected)} watchlist URLs now carry a real crawl date ({len(indexed)} indexed, {len(discovered)} still discovered-only).",
            "next": "Request-indexing cut through. Resume SEO Cycle 2 in the revised order: A2/A4/A6 hygiene → CA-firms pillar (B3) → B2 briefing rationalisation (its 4-week gate expired 2026-08-28 — decision is ripe).",
            "evidence": evidence,
        }

    if weeks is not None and weeks >= STARVED_WEEKS:
        return {
            "code": "STARVED",
            "summary": f"{weeks} weeks after the request, {len(crawled)}/{len(inspected)} watchlist URLs have been crawled; {len(discovered)} remain discovered-only.",
            "next": "Request-indexing did not cut through. Skip hygiene and escalate to the authority/cadence problem directly: open decision D-D (daily briefing cadence), now double-evidenced by crawl starvation and the 2026-09-06 funnel baseline.",
            "evidence": evidence,
        }

    return {
        "code": "TOO_EARLY",
        "summary": f"{weeks if weeks is not None else '?'} weeks since the request and {len(crawled)}/{len(inspected)} crawled — under the {STARVED_WEEKS}-week threshold.",
        "next": "No decision yet. Re-run next week.",
        "evidence": evidence,
    }


# ── Is this run's data believable? ──────────────────────────────────────────
def check_data_sanity(prev: list[Record] | None, curr: list[Record]) -> dict[str, Any]:
    """Google can only re-judge a page it has re-fetched, so a mass "indexed → not indexed"
    flip in which nothing was recrawled is a Search Console data fault, not an SEO event.
    Only URLs present and non-error in BOTH runs count."""
    none: dict[str, Any] = {
        "suspect": False,
        "prev_indexed": 0,
        "regressed": 0,
        "regressed_share": 0,
        "stale_crawl": 0,
        "stale_crawl_share": 0,
        "reason": "no previous run to compare against",
    }
    if not prev:
        return none

    by_url = {p["url"]: p for p in prev}
    prev_indexed = regressed = stale_crawl = 0
    for r in curr:
        p = by_url.get(r["url"])
        if p is None or r["bucket"] == "error" or p["bucket"] == "error":
            continue
        if p["bucket"] != "indexed":
            continue
        prev_indexed += 1
        if r["bucket"] == "indexed":
            continue
        regressed += 1
        # Unchanged crawl timestamp = Google never re-fetched, so it cannot have re-judged.
        if _same_time(p.get("last_crawl_time"), r["last_crawl_time"]):
            stale_crawl += 1

    if prev_indexed == 0:
        return {**none, "reason": "previous run had no indexed URLs in common"}

    regressed_share = regressed / prev_indexed
    stale_share = stale_crawl / regressed if regressed else 0
    suspect = (
        regressed >= SUSPECT_MIN_REGRESSIONS
        and regressed_share >= SUSPECT_REGRESSED_SHARE
        and stale_share >= SUSPECT_STALE_CRAWL_SHARE
    )
    return {
        "suspect": suspect,
        "prev_indexed": prev_indexed,
        "regressed": regressed,
        "regressed_share": _round(regressed_share, 2),
        "stale_crawl": stale_crawl,
        "stale_crawl_share": _round(stale_share, 2),
        "reason": (
            f"{regressed}/{prev_indexed} previously-indexed URLs regressed and {stale_crawl} of them were never recrawled"
            if suspect
            else f"{regressed}/{prev_indexed} regressed ({stale_crawl} without a recrawl) — within normal churn"
        ),
    }


def suspect_verdict(sanity: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "SUSPECT_DATA",
        "summary": f"Search Console returned implausible index data: {sanity['reason']}.",
        "next": (
            "Do NOT act on this run and do not treat it as the new baseline — Google cannot re-judge a page it never re-fetched. "
            "Confirm against reality with a site: search before believing any de-indexation, then re-run in a few hours; the last good run still stands."
        ),
        "evidence": evidence,
    }


# ── Diff vs previous run ────────────────────────────────────────────────────
def diff_runs(
    prev: list[Record] | None, prev_run_at: str | None, curr: list[Record]
) -> dict[str, Any] | None:
    if prev is None:
        return None
    by_url = {p["url"]: p for p in prev}
    diff: dict[str, Any] = {
        "prev_run_at": prev_run_at,
        "compared": 0,
        "changed": [],
        "newly_crawled": [],
        "newly_indexed": [],
        "regressed": [],
    }
    for r in curr:
        p = by_url.get(r["url"])
        if p is None or r["bucket"] == "error" or p["bucket"] == "error":
            continue
        diff["compared"] += 1
        if p["bucket"] != r["bucket"]:
            diff["changed"].append({"url": r["url"], "from": p["bucket"], "to": r["bucket"]})
        if p.get("last_crawl_time") is None and r["last_crawl_time"] is not None:
            diff["newly_crawled"].append(r["url"])
        if p["bucket"] != "indexed" and r["bucket"] == "indexed":
            diff["newly_indexed"].append(r["url"])
        if p["bucket"] == "indexed" and r["bucket"] != "indexed":
            diff["regressed"].append(r["url"])
    return diff


# ── "Crawled – currently not indexed": is it the briefings? ─────────────────
def crawled_not_indexed_breakdown(records: list[Record]) -> dict[str, Any]:
    hits = [r for r in records if r["bucket"] == "crawled_not_indexed"]
    by = {"briefings": 0, "blog": 0, "other": 0}
    for r in hits:
        if r["path"].startswith("/briefings/"):
            by["briefings"] += 1
        elif r["path"].startswith("/blog/"):
            by["blog"] += 1
        else:
            by["other"] += 1
    share = by["briefings"] / len(hits) if hits else 0
    if len(hits) < 5:
        hypothesis = "insufficient"
    elif share >= 0.7:
        hypothesis = "confirmed"
    elif share < 0.5:
        hypothesis = "rejected"
    else:
        hypothesis = "mixed"
    return {
        "total": len(hits),
        "by": by,
        "briefings_share": _round(share, 2),
        "hypothesis": hypothesis,
        "urls": [r["url"] for r in hits],
    }


# ── Human shortlist for the Request Indexing button ─────────────────────────
SHORTLIST_BUCKETS: tuple[str, ...] = ("discovered", "unknown", "crawled_not_indexed")
TIER_LABEL = ("commercial", "blog", "briefing")


def tier_of(path: str) -> int:
    """Commercial pages first, then blog, then briefings."""
    if path.startswith("/briefings/"):
        return 2
    if path.startswith("/blog/"):
        return 1
    return 0


def shortlist(records: list[Record], max_items: int = SHORTLIST_MAX) -> list[dict[str, Any]]:
    eligible = [
        r
        for r in records
        if r["bucket"] in SHORTLIST_BUCKETS
        and r["in_sitemap"]
        and r["requested_indexing_at"] is None
    ]
    eligible.sort(
        key=lambda r: (tier_of(r["path"]), SHORTLIST_BUCKETS.index(r["bucket"]), r["path"])
    )
    return [
        {
            "url": r["url"],
            "bucket": r["bucket"],
            "reason": f"{r['coverage_state'] if r['coverage_state'] is not None else r['bucket']} · {TIER_LABEL[tier_of(r['path'])]}",
        }
        for r in eligible[:max_items]
    ]


# ── Search Console client (lib/seo/gsc.ts) ──────────────────────────────────
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters"
TOKEN_URI = "https://oauth2.googleapis.com/token"
GSC_API = "https://searchconsole.googleapis.com"
TOKEN_HINT = "token exchange failed — is the key revoked or the JSON truncated?"


class GscError(RuntimeError):
    def __init__(self, status_code: int | str, body: str, hint: str) -> None:
        super().__init__(f"GSC HTTP {status_code}: {body[:300]}{f' — {hint}' if hint else ''}")
        self.status_code = status_code
        self.hint = hint


def hint_for(code: int) -> str:
    if code == 403:
        return "service account is not a user on the property, or the Search Console API is not enabled on its GCP project"
    if code == 401:
        return "bearer token rejected"
    if code == 404:
        return "property not found — use the URL-prefix property https://saralprivacy.com/ exactly (trailing slash)"
    if code == 429:
        return "quota exhausted (2,000 inspections/day, 600/min)"
    return ""


def parse_service_account(raw: str, where: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise ValueError(f"{where}: not valid JSON") from None
    if (
        not isinstance(parsed, dict)
        or parsed.get("type") != "service_account"
        or not parsed.get("client_email")
        or not parsed.get("private_key")
    ):
        raise ValueError(
            f"{where}: expected a Google service-account key (type=service_account with client_email + private_key)"
        )
    return {
        "type": "service_account",
        "client_email": str(parsed["client_email"]),
        "private_key": str(parsed["private_key"]),
        "token_uri": str(parsed.get("token_uri") or TOKEN_URI),
    }


def load_service_account() -> tuple[dict[str, str], str] | None:
    """The key comes from `GSC_SERVICE_ACCOUNT_JSON` only (inline JSON)."""
    inline = (settings.GSC_SERVICE_ACCOUNT_JSON or "").strip()
    if not inline:
        return None
    return (
        parse_service_account(inline, "GSC_SERVICE_ACCOUNT_JSON"),
        "env:GSC_SERVICE_ACCOUNT_JSON",
    )


def fetch_access_token(sa: dict[str, str]) -> str:
    """Service-account JWT → bearer token via google-auth."""
    from google.auth.transport.requests import Request  # noqa: PLC0415
    from google.oauth2 import service_account  # noqa: PLC0415

    creds = service_account.Credentials.from_service_account_info(sa, scopes=[GSC_SCOPE])
    try:
        creds.refresh(Request())
    except Exception as exc:  # noqa: BLE001 — google.auth.exceptions.RefreshError & transport errors
        raise GscError("token", str(exc), TOKEN_HINT) from None
    token = getattr(creds, "token", None)
    if not token:
        raise RuntimeError("token exchange returned no access_token")
    return str(token)


class GscApi(Protocol):
    def inspect(self, url: str) -> dict[str, Any]: ...
    def search_analytics(self, start_date: str, end_date: str) -> list[dict[str, Any]]: ...
    def list_sitemaps(self) -> list[dict[str, Any]]: ...
    def submit_sitemap(self, feedpath: str) -> None: ...


class GscClient:
    def __init__(
        self,
        sa: dict[str, str],
        site_url: str,
        *,
        token_fetcher: Callable[[dict[str, str]], str] = fetch_access_token,
        http: httpx.Client | None = None,
    ) -> None:
        self._sa = sa
        self._site_url = site_url
        self._site = quote(site_url, safe="")
        self._token_fetcher = token_fetcher
        self._http = http or httpx.Client(timeout=30)
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _bearer(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() > self._token[1]:
                self._token = (self._token_fetcher(self._sa), time.monotonic() + 50 * 60)
            return self._token[0]

    def _call(self, method: str, url: str, body: Any = None) -> Any:
        attempt = 0
        while True:
            try:
                res = self._http.request(
                    method,
                    url,
                    headers={
                        "authorization": f"Bearer {self._bearer()}",
                        "content-type": "application/json",
                    },
                    content=None if body is None else json.dumps(body),
                    timeout=30,
                )
            except httpx.HTTPError:
                # Network-level failure — retry like a 5xx (6 of 242 hit this on 2026-09-07).
                if attempt < 4:
                    _sleep(0.5 * 2**attempt)
                    attempt += 1
                    continue
                raise
            if (res.status_code == 429 or res.status_code >= 500) and attempt < 4:
                _sleep(0.5 * 2**attempt)
                attempt += 1
                continue
            if not res.is_success:
                raise GscError(res.status_code, res.text, hint_for(res.status_code))
            if res.status_code == 204 or not res.content:
                return None
            return res.json()

    def inspect(self, url: str) -> dict[str, Any]:
        r = self._call(
            "POST",
            f"{GSC_API}/v1/urlInspection/index:inspect",
            {"inspectionUrl": url, "siteUrl": self._site_url, "languageCode": "en-US"},
        )
        return (r or {}).get("inspectionResult") or {}

    def search_analytics(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        r = self._call(
            "POST",
            f"{GSC_API}/webmasters/v3/sites/{self._site}/searchAnalytics/query",
            {
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["page"],
                "rowLimit": 5000,
                "dataState": "final",
            },
        )
        return list((r or {}).get("rows") or [])

    def list_sitemaps(self) -> list[dict[str, Any]]:
        r = self._call("GET", f"{GSC_API}/webmasters/v3/sites/{self._site}/sitemaps")
        return list((r or {}).get("sitemap") or [])

    def submit_sitemap(self, feedpath: str) -> None:
        self._call(
            "PUT", f"{GSC_API}/webmasters/v3/sites/{self._site}/sitemaps/{quote(feedpath, safe='')}"
        )


def make_client(sa: dict[str, str]) -> GscApi:
    """Factory the run uses; tests replace it with a fixture client."""
    return GscClient(sa, SITE)


# ── Sitemap (lib/seo/run.ts) ────────────────────────────────────────────────
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_SITEMAP_INDEX = re.compile(r"<sitemapindex", re.IGNORECASE)


def _http_get(url: str) -> httpx.Response:
    """Tests replace this."""
    return httpx.get(url, headers={"user-agent": "saralprivacy-seo-inspect/1"}, timeout=20)


def fetch_sitemap_urls(base: str, depth: int = 0) -> list[str]:
    """sitemap.xml is a dynamic route — retry a blip rather than blank the run."""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            res = _http_get(base)
            if not res.is_success:
                raise RuntimeError(f"HTTP {res.status_code}")
            xml = res.text
            locs = _LOC.findall(xml)
            if _SITEMAP_INDEX.search(xml) and depth < 2:
                return [u for loc in locs for u in fetch_sitemap_urls(loc, depth + 1)]
            if not locs:
                raise RuntimeError("no <loc> entries")
            return locs
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            _sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{base}: {last_err}")


def resolve_sitemap(
    site: str, prev: dict[str, Any] | None, fallback: list[str] | None, log_line: LogFn
) -> tuple[list[str], bool]:
    """Live sitemap, else the previous run's, else the fallback — never blank the shortlist."""
    sitemap_url = f"{site.rstrip('/')}/sitemap.xml"
    try:
        return fetch_sitemap_urls(sitemap_url), True
    except Exception as exc:  # noqa: BLE001
        from_prev = prev["sitemap_urls"] if prev and prev.get("sitemap_urls") else None
        source = (
            "the previous run's sitemap"
            if from_prev
            else "the fallback sitemap"
            if fallback
            else "the watchlist only"
        )
        log_line(f"sitemap fetch failed after retries ({exc}); using {source}")
        return list(from_prev or fallback or []), False


def merge_ledger(code: dict[str, str], db: dict[str, str]) -> dict[str, str]:
    """Code ledger + DB ledger (ops.seo_index_requests); the DB wins on conflict."""
    return {**code, **db}


# ── The inspection run ──────────────────────────────────────────────────────
def run_inspection(
    *,
    api: GscApi,
    site: str,
    scope: str,
    budget: int,
    analytics: bool,
    submit_sitemap: bool,
    prev: dict[str, Any] | None,
    ledger: dict[str, str],
    sitemap_urls: list[str],
    sitemap_fetched: bool,
    key_source: str,
    dry_run: bool = False,
    now: datetime | None = None,
    log_line: LogFn | None = None,
) -> dict[str, Any]:
    say: LogFn = log_line or (lambda _s: None)
    moment = now or datetime.now(UTC)
    sitemap_url = f"{site.rstrip('/')}/sitemap.xml"

    # Targets: watchlist first, then newcomers / everything, bounded by budget.
    sitemap_set = set(sitemap_urls)
    prev_sitemap = set(prev["sitemap_urls"]) if prev else set()
    newcomers = (
        [u for u in sitemap_urls if u not in prev_sitemap]
        if prev
        else [u for u in sitemap_urls if u not in WATCHLIST]
    )
    say(
        f"sitemap: {len(sitemap_urls)} URLs{'' if sitemap_fetched else ' (not fetched live)'} · {len(newcomers)} newcomers"
    )

    targets: list[str] = list(WATCHLIST)
    extra = sitemap_urls if scope == "full" else newcomers if scope == "newcomers" else []
    seen = set(targets)
    for u in extra:
        if u not in seen:
            seen.add(u)
            targets.append(u)
    cut = max(len(WATCHLIST), budget)
    if len(targets) > cut:
        say(f"budget {budget}: inspecting {cut} of {len(targets)} candidate URLs")
    to_inspect = targets[:cut]

    watch_set = set(WATCHLIST)
    done = 0
    counter = threading.Lock()

    def inspect_one(url: str) -> Record:
        nonlocal done
        try:
            record = to_record(
                url, api.inspect(url), watchlist=watch_set, sitemap=sitemap_set, ledger=ledger
            )
            with counter:
                done += 1
                progress = done
            if progress % 25 == 0:
                say(f"  inspected {progress}/{len(to_inspect)}")
            return record
        except Exception as exc:  # noqa: BLE001
            say(f"  ✗ {url}: {exc}")
            return to_record(
                url, None, watchlist=watch_set, sitemap=sitemap_set, ledger=ledger, error=str(exc)
            )

    with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="gsc") as pool:
        records = list(pool.map(inspect_one, to_inspect))
    errors = sum(1 for r in records if r["bucket"] == "error")
    say(f"inspected {len(records)} ({errors} errors)")

    # Search Analytics, last 28 days (GSC data lags ~3 days).
    if analytics:
        try:
            end = moment - timedelta(days=3)
            start = end - timedelta(days=27)
            rows = api.search_analytics(iso_day(start), iso_day(end))
            by_page = {row["keys"][0]: row for row in rows if row.get("keys")}
            for r in records:
                row = by_page.get(r["url"])
                if row:
                    r["search_28d"] = {
                        "clicks": row.get("clicks", 0),
                        "impressions": row.get("impressions", 0),
                        "position": _round(float(row.get("position", 0)), 1),
                    }
            say(
                f"search analytics: {len(rows)} pages with impressions ({iso_day(start)} → {iso_day(end)})"
            )
        except Exception as exc:  # noqa: BLE001
            say(f"search analytics skipped: {exc}")

    # Sitemaps in GSC (+ optional resubmit).
    sitemaps_in_gsc: list[dict[str, Any]] = []
    submitted = False
    try:
        if submit_sitemap:
            api.submit_sitemap(sitemap_url)
            submitted = True
            say(f"sitemap resubmitted: {sitemap_url}")
        sitemaps_in_gsc = api.list_sitemaps()
    except Exception as exc:  # noqa: BLE001
        say(f"sitemaps API skipped: {exc}")

    # Verdict, diff, shortlist.
    prev_records: list[Record] | None = prev["records"] if prev else None
    prev_discovered = (
        sum(
            1
            for p in prev["records"]
            if p["url"] in watch_set and p["bucket"] in ("discovered", "unknown")
        )
        if prev
        else None
    )
    real_verdict = decide(records, now=moment, prev_discovered=prev_discovered)

    # A mass de-indexation with nothing recrawled is an API fault: override the verdict;
    # the caller must also skip persisting so it never becomes the next baseline.
    sanity = check_data_sanity(prev_records, records)
    if sanity["suspect"]:
        say(
            f"⚠ SUSPECT DATA — {sanity['reason']}; verdict withheld and this run will not be persisted"
        )
    verdict = (
        suspect_verdict(sanity, real_verdict["evidence"]) if sanity["suspect"] else real_verdict
    )

    return {
        "run_id": str(uuid.uuid4()),
        "run_at": iso_z(moment),
        "site": site,
        "scope": scope,
        "dry_run": dry_run,
        "key_source": key_source,
        "sitemap": {
            "url": sitemap_url,
            "fetched": sitemap_fetched,
            "url_count": len(sitemap_urls),
            "urls": sitemap_urls,
            "newcomers": newcomers,
        },
        "sitemaps_in_gsc": sitemaps_in_gsc,
        "sitemap_submitted": submitted,
        "inspected": len(records),
        "errors": errors,
        "buckets": bucket_counts(records),
        "watchlist_buckets": bucket_counts([r for r in records if r["watchlist"]]),
        "verdict": verdict,
        "data_sanity": sanity,
        "diff": diff_runs(prev_records, prev["run_at"] if prev else None, records),
        "crawled_not_indexed": crawled_not_indexed_breakdown(records),
        "shortlist": shortlist(records),
        "urls": records,
    }


# ── Persistence (lib/seo/db.ts) ─────────────────────────────────────────────
def fetch_prev_run(session: Session) -> dict[str, Any] | None:
    run = crud.latest_seo_run(session)
    if run is None:
        return None
    records = [
        {
            "url": i.url,
            "bucket": i.bucket,
            "last_crawl_time": crud.json_safe(i.last_crawl_time),
        }
        for i in crud.list_seo_inspections(session, run.id, order_by="url")
    ]
    return {
        "run_at": crud.json_safe(run.run_at),
        "sitemap_urls": list(run.sitemap_urls or []),
        "records": records,
    }


def ledger_to_map(session: Session) -> dict[str, str]:
    """Ledger rows → the path-keyed map the verdict expects."""
    return {path_of(r.url): r.requested_at.isoformat() for r in crud.list_index_requests(session)}


def persist(session: Session, rep: dict[str, Any]) -> None:
    run_id = uuid.UUID(rep["run_id"])
    run_at = _parse_time(rep["run_at"]) or datetime.now(UTC)
    run = SeoRun(
        id=run_id,
        run_at=run_at,
        site=rep["site"],
        scope=rep["scope"],
        dry_run=rep["dry_run"],
        inspected=rep["inspected"],
        errors=rep["errors"],
        sitemap_url_count=rep["sitemap"]["url_count"],
        sitemap_urls=rep["sitemap"]["urls"],
        verdict_code=rep["verdict"]["code"],
        summary=crud.json_safe(
            {
                "verdict": rep["verdict"],
                "buckets": rep["buckets"],
                "watchlist_buckets": rep["watchlist_buckets"],
                "diff": rep["diff"],
                "crawled_not_indexed": rep["crawled_not_indexed"],
                "shortlist": rep["shortlist"],
                "newcomers": rep["sitemap"]["newcomers"],
                "sitemaps_in_gsc": rep["sitemaps_in_gsc"],
            }
        ),
    )
    inspections = [
        SeoInspection(
            run_id=run_id,
            run_at=run_at,
            url=r["url"],
            path=r["path"],
            watchlist=r["watchlist"],
            in_sitemap=r["in_sitemap"],
            bucket=r["bucket"],
            coverage_state=r["coverage_state"],
            verdict=r["verdict"],
            indexing_state=r["indexing_state"],
            last_crawl_time=_parse_time(r["last_crawl_time"]),
            google_canonical=r["google_canonical"],
            user_canonical=r["user_canonical"],
            robots_txt_state=r["robots_txt_state"],
            page_fetch_state=r["page_fetch_state"],
            referring_urls=r["referring_urls"],
            requested_indexing_at=(
                date.fromisoformat(r["requested_indexing_at"])
                if r["requested_indexing_at"]
                else None
            ),
            clicks_28d=(r["search_28d"] or {}).get("clicks"),
            impressions_28d=(r["search_28d"] or {}).get("impressions"),
            error=r["error"],
            raw=r["raw"],
        )
        for r in rep["urls"]
    ]
    crud.insert_seo_run(session, run, inspections, page=PERSIST_PAGE)


def execute_inspection(
    session: Session,
    *,
    sa: dict[str, str],
    key_source: str,
    scope: str,
    submit_sitemap: bool,
    now: datetime | None = None,
) -> tuple[bool, dict[str, Any]]:
    """One full run, shared by the admin button and the weekly job. Returns the payload the
    old route answered with (success and failure shapes alike)."""
    started = time.monotonic()
    lines: list[str] = []
    say: LogFn = lines.append
    try:
        prev = fetch_prev_run(session)
        ledger = merge_ledger(REQUESTED_INDEXING, ledger_to_map(session))
        urls, fetched = resolve_sitemap(SITE, prev, None, say)
        report = run_inspection(
            api=make_client(sa),
            site=SITE,
            scope=scope,
            budget=DEFAULT_BUDGET,
            analytics=True,
            submit_sitemap=submit_sitemap,
            prev=prev,
            ledger=ledger,
            sitemap_urls=urls,
            sitemap_fetched=fetched,
            key_source=key_source,
            dry_run=False,
            now=now,
            log_line=say,
        )
        # A suspect run is shown but never stored — it must not become the baseline.
        suspect = bool((report.get("data_sanity") or {}).get("suspect"))
        if suspect:
            say(
                f"NOT persisted — suspect data ({report['data_sanity']['reason']}); the last good run remains the baseline"
            )
        else:
            persist(session, report)
        return True, {
            "ok": True,
            "run_id": report["run_id"],
            "persisted": not suspect,
            "verdict": report["verdict"]["code"],
            "summary": report["verdict"]["summary"],
            "inspected": report["inspected"],
            "errors": report["errors"],
            "shortlist": len(report["shortlist"]),
            "newcomers": len(report["sitemap"]["newcomers"]),
            "durationMs": int((time.monotonic() - started) * 1000),
            "log": lines,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        log.exception("SEO inspection failed")
        return False, {
            "ok": False,
            "error": str(exc) or "Unknown error",
            "durationMs": int((time.monotonic() - started) * 1000),
            "log": lines,
        }


# ── Routes ──────────────────────────────────────────────────────────────────
class InspectIn(BaseModel):
    scope: Any = None
    submitSitemap: Any = None  # noqa: N815 — the page's JSON field name


class IndexRequestIn(BaseModel):
    url: Any = None


def _load_sa_or_500() -> tuple[dict[str, str], str]:
    try:
        loaded = load_service_account()
    except ValueError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc))
    if loaded is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, GSC_NOT_CONFIGURED)
    return loaded


@router.post("/seo-inspect", status_code=status.HTTP_202_ACCEPTED)
def start_inspection(
    session: SessionDep,
    background: BackgroundTasks,
    actor: AdminUser,
    body: InspectIn | None = None,
) -> dict[str, Any]:
    payload = body or InspectIn()
    scope = payload.scope if payload.scope in SCOPES else "newcomers"
    submit = payload.submitSitemap is True
    sa, key_source = _load_sa_or_500()
    return start_task(
        session,
        background,
        kind="seo-inspect",
        actor=actor,
        work=lambda s: execute_inspection(
            s, sa=sa, key_source=key_source, scope=scope, submit_sitemap=submit
        ),
    )


INSPECTION_FIELDS = (
    "url",
    "path",
    "watchlist",
    "in_sitemap",
    "bucket",
    "coverage_state",
    "last_crawl_time",
    "requested_indexing_at",
    "clicks_28d",
    "impressions_28d",
    "google_canonical",
    "error",
)
RUN_FIELDS = (
    "id",
    "run_at",
    "site",
    "scope",
    "dry_run",
    "inspected",
    "errors",
    "sitemap_url_count",
    "verdict_code",
    "summary",
)


@router.get("/seo")
def read_seo(session: SessionDep, _admin: AdminUser) -> dict[str, Any]:
    """What /admin/seo rendered from lib/seo/db.ts: the last 12 runs, the newest run's
    inspections (by path) and the request-indexing ledger."""
    runs = crud.list_seo_runs(session, 12)
    ledger = [
        {"url": r.url, "requested_at": r.requested_at.isoformat(), "note": r.note}
        for r in crud.list_index_requests(session)
    ]
    if not runs:
        return {"state": "empty", "ledger": ledger}
    run_rows = [crud.json_safe({f: getattr(r, f) for f in RUN_FIELDS}) for r in runs]
    rows = [
        crud.json_safe({f: getattr(i, f) for f in INSPECTION_FIELDS})
        for i in crud.list_seo_inspections(session, runs[0].id)
    ]
    return {
        "state": "ready",
        "runs": run_rows,
        "latest": run_rows[0],
        "rows": rows,
        "ledger": ledger,
    }


_ON_SITE = re.compile(r"^https://saralprivacy\.com/")


@router.post("/seo/index-requests")
def mark_requested(body: IndexRequestIn, session: SessionDep, _admin: AdminUser) -> dict[str, Any]:
    """ "Mark requested" — the GSC Request Indexing button was pressed for this URL, so the
    watcher never shortlists it again (quota rule)."""
    url = str(body.url or "").strip()
    if not _ON_SITE.match(url):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, URL_NOT_ON_SITE)
    crud.upsert_index_request(session, url, datetime.now(UTC).date(), INDEX_REQUEST_NOTE)
    return {"success": True}
