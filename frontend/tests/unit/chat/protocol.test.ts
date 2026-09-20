import { describe, expect, it } from "vitest";

import { CHAT_ENDPOINTS, META_SENTINEL, parseChatResponse } from "@/lib/chat/protocol";

describe("lib/chat/protocol", () => {
  it("uses U+001E as the sentinel, byte-identical to the backend constant", () => {
    expect(META_SENTINEL).toBe("");
    expect(META_SENTINEL.charCodeAt(0)).toBe(0x1e);
  });

  it("points every widget call at the backend through the proxy", () => {
    expect(CHAT_ENDPOINTS).toEqual({
      chat: "/api/proxy/api/v1/chat",
      feedback: "/api/proxy/api/v1/chat/feedback",
      handoff: "/api/proxy/api/v1/chat/handoff",
      health: "/api/proxy/api/v1/chat/health",
    });
  });

  it("splits a complete body into text and meta", () => {
    const meta = { citations: [], actions: [], confidence: "high", refusal: false };
    const parsed = parseChatResponse(`Hello there.${META_SENTINEL}${JSON.stringify(meta)}`);
    expect(parsed.text).toBe("Hello there.");
    expect(parsed.meta).toMatchObject({ confidence: "high", refusal: false });
  });

  it("returns the whole body as text when no sentinel arrived", () => {
    expect(parseChatResponse("partial")).toEqual({ text: "partial", meta: null });
  });

  it("keeps the text but drops meta when the JSON is truncated", () => {
    expect(parseChatResponse(`answer${META_SENTINEL}{"citations":[`)).toEqual({
      text: "answer",
      meta: null,
    });
  });
});
