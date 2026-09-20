import { beforeEach, describe, expect, it, vi } from "vitest";

import { ADMIN_API, pollTask } from "@/app/(backoffice)/admin/_lib/pollTask";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("pollTask", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch");
  });

  it("polls the task until it is done and returns its result", async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(json({ status: "running", result: null }))
      .mockRejectedValueOnce(new Error("blip"))
      .mockResolvedValueOnce(json({ status: "done", result: { ok: true, persisted: 20 } }));

    const out = await pollTask<{ ok: boolean }>("abc", { intervalMs: 0 });

    expect(out).toEqual({ status: "done", result: { ok: true, persisted: 20 } });
    expect(vi.mocked(global.fetch).mock.calls[0][0]).toBe(`${ADMIN_API}/tasks/abc`);
    expect(ADMIN_API).toBe("/api/proxy/api/v1/admin");
  });

  it("reports a failed task with its payload", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ status: "failed", result: { ok: false, error: "quota" } }));
    expect(await pollTask("t", { intervalMs: 0 })).toEqual({ status: "failed", result: { ok: false, error: "quota" } });
  });

  it("stops on 401", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ detail: "Session expired." }, 401));
    expect(await pollTask("t", { intervalMs: 0 })).toEqual({ status: "unauthorized", result: null });
  });

  it("gives up after the timeout", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ status: "running" }));
    expect(await pollTask("t", { intervalMs: 1, timeoutMs: 5 })).toEqual({ status: "timeout", result: null });
  });
});
