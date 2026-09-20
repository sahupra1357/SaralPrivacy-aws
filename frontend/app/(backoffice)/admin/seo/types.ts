/**
 * Shapes GET /api/v1/admin/seo returns — the same rows lib/seo/db.ts used to read from
 * ops.seo_runs / ops.seo_inspections / ops.seo_index_requests.
 */
export type Bucket = "indexed" | "discovered" | "crawled_not_indexed" | "unknown" | "excluded" | "other" | "error";

export const BUCKETS: readonly Bucket[] = ["indexed", "discovered", "crawled_not_indexed", "unknown", "excluded", "other", "error"];

export type VerdictCode = "QUEUE_MOVED" | "STARVED" | "TOO_EARLY" | "INSUFFICIENT_DATA" | "SUSPECT_DATA";

export type Verdict = {
  code: VerdictCode;
  summary: string;
  next: string;
  evidence: Record<string, number | null>;
};

export type Diff = {
  prev_run_at: string | null;
  compared: number;
  changed: Array<{ url: string; from: Bucket; to: Bucket }>;
  newly_crawled: string[];
  newly_indexed: string[];
  regressed: string[];
};

export type Breakdown = {
  total: number;
  by: { briefings: number; blog: number; other: number };
  briefings_share: number;
  hypothesis: "confirmed" | "rejected" | "mixed" | "insufficient";
  urls: string[];
};

export type ShortlistItem = { url: string; bucket: Bucket; reason: string };

export type RunSummary = {
  verdict: Verdict;
  buckets: Record<Bucket, number>;
  watchlist_buckets: Record<Bucket, number>;
  diff: Diff | null;
  crawled_not_indexed: Breakdown;
  shortlist: ShortlistItem[];
  newcomers: string[];
  sitemaps_in_gsc: Array<Record<string, unknown>>;
};

export type RunRow = {
  id: string;
  run_at: string;
  site: string;
  scope: string;
  dry_run: boolean;
  inspected: number;
  errors: number;
  sitemap_url_count: number;
  verdict_code: VerdictCode;
  summary: RunSummary | null;
};

export type InspectionRow = {
  url: string;
  path: string;
  watchlist: boolean;
  in_sitemap: boolean;
  bucket: Bucket;
  coverage_state: string | null;
  last_crawl_time: string | null;
  requested_indexing_at: string | null;
  clicks_28d: number | null;
  impressions_28d: number | null;
  google_canonical: string | null;
  error: string | null;
};

export type LedgerRow = { url: string; requested_at: string; note: string | null };

export type SeoPayload =
  | { state: "empty"; ledger: LedgerRow[] }
  | { state: "ready"; runs: RunRow[]; latest: RunRow; rows: InspectionRow[]; ledger: LedgerRow[] };
