/**
 * The owned funnel sink now lives on the FastAPI backend and is reached through the
 * Next.js proxy. Everything else about `track()` is unchanged: GA still fires, the
 * session id is sticky, and a failing sink must never surface to the user.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is hoisted above top-level consts, so the spy must be created with vi.hoisted.
const { notice } = vi.hoisted(() => ({ notice: vi.fn() }));
vi.mock("@/lib/analytics", () => ({ trackEvent: { notice } }));

import { track } from "@/lib/notice-pack/track";

const PROXY_URL = "/api/proxy/api/v1/notices/events";

function lastCall() {
  const fetchMock = global.fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.at(-1) as [string, RequestInit];
}

describe("notice-pack track()", () => {
  beforeEach(() => {
    notice.mockClear();
    global.fetch = vi.fn(() => Promise.resolve(new Response("{}"))) as unknown as typeof fetch;
    sessionStorage.clear();
  });

  it("posts the event to the backend through the proxy", () => {
    track("notice_pdf_downloaded", { sector: "d2c" });
    const [url, init] = lastCall();
    expect(url).toBe(PROXY_URL);
    expect(init.method).toBe("POST");
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("sends the event name and the non-PII params", () => {
    track("notice_score_calculated", { sector: "clinic", score: 71 });
    const body = JSON.parse(lastCall()[1].body as string);
    expect(body.name).toBe("notice_score_calculated");
    expect(body.sector).toBe("clinic");
    expect(body.score).toBe(71);
    expect(typeof body.session_id).toBe("string");
    expect(body.session_id.length).toBeGreaterThan(0);
  });

  it("reuses one session id across events", () => {
    track("notice_builder_started");
    const first = JSON.parse(lastCall()[1].body as string).session_id;
    track("notice_html_copied");
    expect(JSON.parse(lastCall()[1].body as string).session_id).toBe(first);
  });

  it("still fires the GA event", () => {
    track("dsar_cta_clicked", { sector: "law" });
    expect(notice).toHaveBeenCalledWith("dsar_cta_clicked", { sector: "law" });
  });

  it("swallows a failing sink so the UI never breaks", () => {
    global.fetch = vi.fn(() => Promise.reject(new Error("offline"))) as unknown as typeof fetch;
    expect(() => track("mini_notice_copied")).not.toThrow();
  });
});
