import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/analytics", () => ({
  trackEvent: {
    chatMessageSent: vi.fn(),
    chatFeedback: vi.fn(),
    chatEscalation: vi.fn(),
    chatHandoffSubmitted: vi.fn(),
    chatOpenerAnswered: vi.fn(),
    chatMemoryCleared: vi.fn(),
  },
}));
vi.mock("@/lib/abuseGuard", () => ({ HONEYPOT_FIELD: "hp_url" }));

import { useSetuChat } from "@/components/chat/useSetuChat";
import { META_SENTINEL } from "@/lib/chat/protocol";

const META = {
  citations: [{ title: "Consent under DPDPA", url: "/learn/consent", tier: 1 }],
  actions: [{ type: "open_url", label: "Consent under DPDPA", url: "/learn/consent" }],
  confidence: "high",
  refusal: false,
  piiWarning: false,
  suggestedFollowups: ["What makes consent valid?"],
  journey: "J4",
  animation: { state: "pointing" },
  disclaimer: "Educational only — not legal advice.",
  escalation: { reason: "explicit_ask" },
  sig: "a".repeat(32),
};

/** A body delivered in chunks, the way the backend's StreamingResponse arrives. */
function streamed(...chunks: string[]): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/plain; charset=utf-8" } });
}

function lastCall(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const calls = fetchMock.mock.calls;
  return calls[calls.length - 1] as [string, RequestInit];
}

describe("useSetuChat data layer", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("streams a turn from the backend chat route and applies the meta block", async () => {
    fetchMock.mockResolvedValue(
      streamed("Consent must be ", "free and specific.", `${META_SENTINEL}${JSON.stringify(META)}`)
    );
    const { result } = renderHook(() => useSetuChat("/learn/consent"));
    await waitFor(() => expect(result.current.state).not.toBeNull());

    await act(async () => {
      await result.current.send("Do I need consent?");
    });

    const [url, init] = lastCall(fetchMock);
    expect(url).toBe("/api/proxy/api/v1/chat");
    expect(init.method).toBe("POST");
    const sent = JSON.parse(String(init.body));
    expect(sent).toMatchObject({ message: "Do I need consent?", pageUrl: "/learn/consent" });
    expect(sent.sessionId).toBeTruthy();

    const setu = result.current.messages.find((m) => m.role === "setu");
    expect(setu?.text).toBe("Consent must be free and specific.");
    expect(setu?.meta?.citations[0].url).toBe("/learn/consent");
    expect(setu?.streaming).toBe(false);

    // Server-issued meta drives the session memory.
    expect(result.current.state?.journey).toBe("J4");
    expect(result.current.state?.pagesShown).toContain("/learn/consent");
    expect(result.current.state?.messageCount).toBe(1);
    expect(result.current.status).toBe("idle");
  });

  it("sends Setu's prior turns back with the signature the server issued", async () => {
    fetchMock.mockResolvedValue(streamed(`First answer.${META_SENTINEL}${JSON.stringify(META)}`));
    const { result } = renderHook(() => useSetuChat("/"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.send("first");
    });

    fetchMock.mockResolvedValue(streamed(`Second.${META_SENTINEL}${JSON.stringify(META)}`));
    await act(async () => {
      await result.current.send("second");
    });

    const history = JSON.parse(String(lastCall(fetchMock)[1].body)).history;
    expect(history).toEqual([
      { role: "user", content: "first" },
      { role: "assistant", content: "First answer.", sig: META.sig },
    ]);
  });

  it("shows the rate-limit line on a 429", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ error: "You've asked a lot — give me a minute and try again." }), {
        status: 429,
      })
    );
    const { result } = renderHook(() => useSetuChat("/"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.send("hello");
    });
    const setu = result.current.messages.find((m) => m.role === "setu");
    expect(setu?.text).toBe("You've asked a lot — give me a minute and try again.");
    expect(setu?.error).toBe(true);
  });

  it("shows the generic error line on any other failure status", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 500 }));
    const { result } = renderHook(() => useSetuChat("/"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.send("hello");
    });
    expect(result.current.messages.find((m) => m.role === "setu")?.text).toBe(
      "Something went wrong on my side — please try that again."
    );
  });

  it("shows the offline line when the network fails", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const { result } = renderHook(() => useSetuChat("/"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.send("hello");
    });
    expect(result.current.messages.find((m) => m.role === "setu")?.text).toBe(
      "I've lost connection — check your network and retry."
    );
  });

  it("posts thumbs-down feedback with the failing question to the backend", async () => {
    fetchMock.mockResolvedValue(streamed(`Answer.${META_SENTINEL}${JSON.stringify(META)}`));
    const { result } = renderHook(() => useSetuChat("/faq"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    await act(async () => {
      await result.current.send("my question");
    });

    fetchMock.mockResolvedValue(new Response('{"stored":true}', { status: 200 }));
    const turn = result.current.messages.find((m) => m.role === "setu")!;
    act(() => result.current.sendFeedback(turn, false));

    const [url, init] = lastCall(fetchMock);
    expect(url).toBe("/api/proxy/api/v1/chat/feedback");
    expect(JSON.parse(String(init.body))).toMatchObject({
      turnId: turn.id,
      helpful: false,
      pageUrl: "/faq",
      failureKind: "thumbs_down",
      question: "my question",
    });
  });

  it("submits the consented handoff to the backend and records the offer", async () => {
    fetchMock.mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    const { result } = renderHook(() => useSetuChat("/learn/consent"));
    await waitFor(() => expect(result.current.state).not.toBeNull());

    let outcome: { ok: boolean } = { ok: false };
    await act(async () => {
      outcome = await result.current.submitHandoff(
        { name: "Asha", email: "asha@example.in", honeypot: "" },
        "explicit_ask"
      );
    });

    expect(outcome.ok).toBe(true);
    const [url, init] = lastCall(fetchMock);
    expect(url).toBe("/api/proxy/api/v1/chat/handoff");
    expect(JSON.parse(String(init.body))).toMatchObject({
      name: "Asha",
      email: "asha@example.in",
      consent: true,
      hp_url: "",
      reason: "explicit_ask",
      pageUrl: "/learn/consent",
    });
    expect(result.current.state?.handoffOffered).toBe(true);
    expect(result.current.state?.consentToContact).toBe(true);
  });

  it("reports a failed handoff so the form can fall back to /contact", async () => {
    fetchMock.mockResolvedValue(
      new Response('{"error":"We couldn\'t send that just now. Please use the contact page."}', {
        status: 500,
      })
    );
    const { result } = renderHook(() => useSetuChat("/"));
    await waitFor(() => expect(result.current.state).not.toBeNull());
    let outcome: { ok: boolean } = { ok: true };
    await act(async () => {
      outcome = await result.current.submitHandoff(
        { name: "Asha", email: "asha@example.in", honeypot: "" },
        "explicit_ask"
      );
    });
    expect(outcome.ok).toBe(false);
    expect(result.current.state?.handoffOffered).toBe(false);
  });
});
