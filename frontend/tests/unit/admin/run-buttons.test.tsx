/**
 * "Run now" buttons on /admin/citations and /admin/seo: start a backend background task,
 * poll it, then render the same messages the synchronous routes used to produce.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/app/(backoffice)/admin/_lib/pollTask", () => ({
  ADMIN_API: "/api/proxy/api/v1/admin",
  pollTask: vi.fn(),
}));

import { pollTask } from "@/app/(backoffice)/admin/_lib/pollTask";
import CitationsRunButton from "@/app/(backoffice)/admin/citations/RunButton";
import SeoRunButton from "@/app/(backoffice)/admin/seo/RunButton";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("citations RunButton", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(json({ ok: true, task_id: "t1", status: "running" }, 202));
    vi.mocked(pollTask).mockReset();
  });

  it("starts the panel, waits for the task and reports the cite rate", async () => {
    vi.mocked(pollTask).mockResolvedValue({
      status: "done",
      result: {
        ok: true,
        persisted: 20,
        summary: { total: 20, cited: 3, mentioned: 1, errored: 2, cleanTotal: 18, citeRate: 3 / 18, byEngine: {} },
      },
    });
    render(<CitationsRunButton />);

    await userEvent.click(screen.getByRole("button", { name: /Run Panel Now/ }));

    expect(
      await screen.findByText(
        "Done — 3 of 18 clean prompts cited saralprivacy (16.7% cite rate) · 2 errored. Persisted 20 rows. Refreshing in 2s…",
      ),
    ).toBeInTheDocument();
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/admin/aeo-panel-run");
    expect((init as RequestInit).method).toBe("POST");
    expect(pollTask).toHaveBeenCalledWith("t1");
  });

  it("shows the backend detail when the run cannot start", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ detail: "OPENROUTER_API_KEY not configured in Vercel env vars" }, 500));
    render(<CitationsRunButton />);
    await userEvent.click(screen.getByRole("button", { name: /Run Panel Now/ }));
    expect(await screen.findByText("OPENROUTER_API_KEY not configured in Vercel env vars")).toBeInTheDocument();
    expect(pollTask).not.toHaveBeenCalled();
  });

  it("shows the run's error when the task failed", async () => {
    vi.mocked(pollTask).mockResolvedValue({ status: "failed", result: { ok: false, error: "executor exploded" } });
    render(<CitationsRunButton />);
    await userEvent.click(screen.getByRole("button", { name: /Run Panel Now/ }));
    expect(await screen.findByText("executor exploded")).toBeInTheDocument();
  });
});

describe("seo RunButton", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch").mockResolvedValue(json({ ok: true, task_id: "t2", status: "running" }, 202));
    vi.mocked(pollTask).mockReset();
  });

  it("posts the chosen scope and renders the verdict line", async () => {
    vi.mocked(pollTask).mockResolvedValue({
      status: "done",
      result: { ok: true, verdict: "TOO_EARLY", summary: "3 weeks since the request.", inspected: 19, errors: 0, newcomers: 2, shortlist: 1, durationMs: 42000 },
    });
    render(<SeoRunButton />);

    await userEvent.selectOptions(screen.getByRole("combobox"), "watchlist");
    await userEvent.click(screen.getByRole("button", { name: /Run now/ }));

    expect(
      await screen.findByText(
        "TOO_EARLY — 3 weeks since the request. Inspected 19 (0 errors), 2 newcomers, 1 on the shortlist, 42s. Refreshing…",
      ),
    ).toBeInTheDocument();
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe("/api/proxy/api/v1/admin/seo-inspect");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ scope: "watchlist" });
  });

  it("explains an expired session", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ detail: "Session expired. Please sign in again." }, 401));
    render(<SeoRunButton />);
    await userEvent.click(screen.getByRole("button", { name: /Run now/ }));
    expect(
      await screen.findByText("Your 8-hour admin session has expired. Reload this page, sign in again, then re-run."),
    ).toBeInTheDocument();
  });

  it("reports a timeout while waiting", async () => {
    vi.mocked(pollTask).mockResolvedValue({ status: "timeout", result: null });
    render(<SeoRunButton />);
    await userEvent.click(screen.getByRole("button", { name: /Run now/ }));
    await waitFor(() => expect(screen.getByText(/^Timed out at \d+s \(300 s ceiling\)\.$/)).toBeInTheDocument());
  });
});
