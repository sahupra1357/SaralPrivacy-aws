/**
 * Briefing/blog data sources read the backend (lib/api apiGet) with the same ISR tags
 * and revalidate windows the lib/db queries used.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));
vi.mock("next/cache", () => ({
  unstable_cache: (fn: (...a: unknown[]) => unknown) => fn,
  revalidateTag: vi.fn(),
  revalidatePath: vi.fn(),
}));
vi.mock("@/lib/api", async () => {
  class ApiError extends Error {
    constructor(public status: number, public body: unknown) {
      super(`API ${status}`);
    }
  }
  return { ApiError, apiGet: vi.fn(), apiPost: vi.fn() };
});

import { apiGet } from "@/lib/api";
import { getArchive } from "@/lib/data/briefings-archive";
import { getBriefingCountLabel, getPublishedBriefings } from "@/lib/data/briefings-source";

function doc(i: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: `id-${i}`,
    $id: `id-${i}`,
    $createdAt: `2026-01-${String((i % 28) + 1).padStart(2, "0")}T00:00:00+00:00`,
    created_at: null,
    slug: `Slug-${i}`,
    title: `Briefing ${i}`,
    status: "approved",
    industries: '["ca-firms"]',
    tags: '["checklist"]',
    category: "fix",
    why_it_matters: JSON.stringify({ save: `Save ${i}`, inf_title: `Inf ${i}`, week_theme: "Act now" }),
    action_checklist: '["First fix"]',
    infographic_base64: "https://cdn.test/x.jpg",
    ...extra,
  };
}

describe("briefings-archive getArchive", () => {
  beforeEach(() => vi.mocked(apiGet).mockReset());

  it("pages through /briefings with the briefings tag and normalises documents", async () => {
    const page1 = Array.from({ length: 100 }, (_, i) => doc(i));
    vi.mocked(apiGet)
      .mockResolvedValueOnce({ docs: page1, total: 101 })
      .mockResolvedValueOnce({ docs: [doc(100)], total: 101 });

    const all = await getArchive();

    expect(apiGet).toHaveBeenNthCalledWith(1, "/briefings?limit=100&offset=0", { tags: ["briefings"], revalidate: 3600 });
    expect(apiGet).toHaveBeenNthCalledWith(2, "/briefings?limit=100&offset=100", { tags: ["briefings"], revalidate: 3600 });
    expect(all).toHaveLength(101);
    const first = all.find((b) => b.id === "id-0")!;
    expect(first).toMatchObject({
      sector: "ca-firms",
      stage: "fix",
      verdict: "Save 0",
      infTitle: "Inf 0",
      fixToday: "First fix",
      image: "https://cdn.test/x.jpg",
    });
  });
});

describe("briefings-source", () => {
  beforeEach(() => vi.mocked(apiGet).mockReset());

  it("returns lowercased slugs with real dates", async () => {
    vi.mocked(apiGet).mockResolvedValueOnce({ docs: [doc(1, { published_at: "2026-02-03" })], total: 1 });
    const refs = await getPublishedBriefings();
    expect(refs).toEqual([{ slug: "slug-1", updated: new Date("2026-02-03") }]);
  });

  it("degrades to what it has when the backend is down", async () => {
    vi.mocked(apiGet).mockRejectedValueOnce(new Error("down"));
    expect(await getPublishedBriefings()).toEqual([]);
  });

  it("never overclaims the public count", async () => {
    vi.mocked(apiGet).mockResolvedValueOnce({ docs: Array.from({ length: 23 }, (_, i) => doc(i)), total: 23 });
    expect(await getBriefingCountLabel()).toBe("20+");
  });
});
