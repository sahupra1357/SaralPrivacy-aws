/**
 * Server-rendered admin pages now read the backend through `_lib/backend` (bearer from the
 * HttpOnly cookie) instead of lib/db / lib/seo/db.
 */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/app/(backoffice)/admin/_lib/backend", () => ({ adminGet: vi.fn(), adminPost: vi.fn() }));
vi.mock("@/app/(backoffice)/admin/seo/actions", () => ({ markRequested: vi.fn() }));
vi.mock("@/app/(backoffice)/admin/seo/RunButton", () => ({ default: () => <div>seo-run-button</div> }));
vi.mock("@/app/(backoffice)/admin/citations/RunButton", () => ({ default: () => <div>aeo-run-button</div> }));
vi.mock("@/app/(backoffice)/admin/bloggers/BloggersClient", () => ({
  default: ({ initialBloggers }: { initialBloggers: Array<{ email: string }> }) => (
    <ul>{initialBloggers.map((b) => <li key={b.email}>{b.email}</li>)}</ul>
  ),
}));

import { adminGet } from "@/app/(backoffice)/admin/_lib/backend";
import { ApiError } from "@/lib/api";
import AdminDashboard from "@/app/(backoffice)/admin/page";
import BloggersPage from "@/app/(backoffice)/admin/bloggers/page";
import CitationsAdmin from "@/app/(backoffice)/admin/citations/page";
import SeoAdmin from "@/app/(backoffice)/admin/seo/page";

vi.mock("@/lib/api", async () => {
  class ApiError extends Error {
    constructor(public status: number, public body: unknown) {
      super(`API ${status}`);
    }
  }
  return { ApiError, apiGet: vi.fn(), apiPost: vi.fn() };
});

const NOW = new Date().toISOString();

describe("admin dashboard", () => {
  beforeEach(() => vi.mocked(adminGet).mockReset());

  it("renders counts, the activity feed and the risk split from /admin/dashboard", async () => {
    vi.mocked(adminGet).mockResolvedValue({
      counts: { leads: 4, subscribers: 12, downloads: 3, assessments: 10, survey_responses: 2, briefings: 5 },
      recent: {
        subscribers: [{ $id: "s1", $createdAt: NOW, name: "Neha Subscriber", email: "neha@example.com" }],
        assessments: [{ $id: "a1", $createdAt: NOW, email: "owner@clinic.test", industry: "health", risk_level: "red" }],
      },
      risk: { green: 5, amber: 3, red: 2, total: 10 },
    });

    render(await AdminDashboard());

    expect(adminGet).toHaveBeenCalledWith("/admin/dashboard");
    // The name appears in both the activity feed and the recent-subscribers list.
    expect(screen.getAllByText("Neha Subscriber").length).toBeGreaterThan(0);
    expect(screen.getByText("owner@clinic.test")).toBeInTheDocument();
    expect(screen.getByText("Assessment Risk Distribution")).toBeInTheDocument();
  });

  it("falls back to zeros when the backend is down", async () => {
    vi.mocked(adminGet).mockRejectedValue(new Error("down"));
    render(await AdminDashboard());
    expect(
      screen.getByText("No activity yet. Actions will appear here as visitors engage with the site."),
    ).toBeInTheDocument();
  });
});

describe("bloggers page", () => {
  it("hands the backend list to the client component", async () => {
    vi.mocked(adminGet).mockResolvedValue({ bloggers: [{ email: "asha@example.com" }] });
    render(await BloggersPage());
    expect(adminGet).toHaveBeenCalledWith("/admin/bloggers");
    expect(screen.getByText("asha@example.com")).toBeInTheDocument();
  });

  it("renders an empty list when the call fails", async () => {
    vi.mocked(adminGet).mockRejectedValue(new Error("down"));
    render(await BloggersPage());
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });
});

describe("citations page", () => {
  it("reads the latest 500 citation rows through the data route", async () => {
    vi.mocked(adminGet).mockResolvedValue({
      documents: [
        {
          $id: "c1", $createdAt: NOW, run_id: "r1", date: "2026-09-14", week_num: 38, engine: "claude",
          engine_label: "Claude", query_id: "Q1", query_text: "q", cited: "Yes", position: 1,
          cited_page: "https://saralprivacy.com/x", competitors: "[]", content_snippet: "", duration_ms: 5,
          error_message: null,
        },
      ],
      total: 1,
    });
    render(await CitationsAdmin());
    expect(adminGet).toHaveBeenCalledWith("/admin/data?collection=ai_citations&limit=500");
    expect(screen.getByText("AEO Citation Panel")).toBeInTheDocument();
    expect(screen.getByText("aeo-run-button")).toBeInTheDocument();
  });
});

describe("seo page", () => {
  it("shows the empty state with the run button", async () => {
    vi.mocked(adminGet).mockResolvedValue({ state: "empty", ledger: [] });
    render(await SeoAdmin());
    expect(adminGet).toHaveBeenCalledWith("/admin/seo");
    expect(screen.getByText("seo-run-button")).toBeInTheDocument();
    expect(screen.getByText(/No run recorded yet/)).toBeInTheDocument();
  });

  it("renders the verdict and shortlist of the latest run", async () => {
    const run = {
      id: "r1", run_at: "2026-09-14T04:00:00+00:00", site: "https://saralprivacy.com/", scope: "full",
      dry_run: false, inspected: 19, errors: 0, sitemap_url_count: 19, verdict_code: "STARVED",
      summary: {
        verdict: { code: "STARVED", summary: "6 weeks after the request.", next: "Escalate.", evidence: {} },
        buckets: { indexed: 1, discovered: 17, crawled_not_indexed: 1, unknown: 0, excluded: 0, other: 0, error: 0 },
        watchlist_buckets: { indexed: 0, discovered: 17, crawled_not_indexed: 0, unknown: 0, excluded: 0, other: 0, error: 0 },
        diff: null,
        crawled_not_indexed: { total: 1, by: { briefings: 0, blog: 1, other: 0 }, briefings_share: 0, hypothesis: "insufficient", urls: [] },
        shortlist: [{ url: "https://saralprivacy.com/learn/consent", bucket: "discovered", reason: "discovered · commercial" }],
        newcomers: [],
        sitemaps_in_gsc: [],
      },
    };
    vi.mocked(adminGet).mockResolvedValue({ state: "ready", runs: [run], latest: run, rows: [], ledger: [] });

    render(await SeoAdmin());

    expect(screen.getByText("6 weeks after the request.")).toBeInTheDocument();
    expect(screen.getByText("discovered · commercial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Mark requested" })).toBeInTheDocument();
  });

  it("shows the missing-tables notice when the backend answers 500", async () => {
    vi.mocked(adminGet).mockRejectedValue(new ApiError(500, { detail: "boom" }));
    render(await SeoAdmin());
    expect(screen.getByText(/The ops.seo_\* tables are missing/)).toBeInTheDocument();
  });
});
