// Two-phase stream protocol (spec §5.5) shared by the FastAPI chat route
// (backend/app/api/routes/chat.py) and the widget.
// Phase A: plain text tokens. Phase B: META_SENTINEL + ChatMeta JSON.
//
// The ChatMeta shape lives here now that the server half (orchestrate.ts) has moved to
// backend/app/services/chat/orchestrate.py. Keep the two in step: every field below is
// emitted by ChatMeta.to_dict() there, and nothing else is.

import type { IndustrySlug } from "./site-routing.ts";
import type { JourneyId } from "./journeys.ts";

/** U+001E RECORD SEPARATOR — never appears in prose or JSON. */
export const META_SENTINEL = "";

export interface ChatCitation {
  title: string;
  url: string;
  tier: number;
}

export interface ChatAction {
  type: "open_url";
  label: string;
  url: string;
}

/**
 * Why the human door opened this turn (outcome-layer spec §4.2/§6.1).
 * Server-computed like every other ChatMeta field — the model cannot reach it.
 */
export type EscalationReason =
  | "explicit_ask"
  | "journey_stalled"
  | "repeat_refusal"
  | "negative_feedback";

export interface ChatMeta {
  citations: ChatCitation[];
  actions: ChatAction[];
  confidence: "high" | "low";
  refusal: boolean;
  piiWarning: boolean;
  suggestedFollowups: string[];
  journey?: JourneyId;
  industry?: IndustrySlug;
  animation: { state: "pointing" | "unsure" | "speaking" };
  disclaimer: string;
  /**
   * Present when this turn should offer the human door. Absent when it should
   * not — including when the session has already been offered once, which is
   * the §2.1 rule that keeps Setu a guide rather than a closer.
   */
  escalation?: { reason: EscalationReason };
  /** HMAC over this answer, so the next turn can tell our transcript from a
   *  forged one. Empty when CHAT_HISTORY_SECRET is unset on the backend. */
  sig?: string;
}

/** Backend paths, reached through the Next.js proxy (app/api/proxy/[...path]). */
export const CHAT_ENDPOINTS = {
  chat: "/api/proxy/api/v1/chat",
  feedback: "/api/proxy/api/v1/chat/feedback",
  handoff: "/api/proxy/api/v1/chat/handoff",
  health: "/api/proxy/api/v1/chat/health",
} as const;

export interface ParsedChatResponse {
  text: string;
  meta: ChatMeta | null;
}

/** Split a fully-received response body into text + meta. */
export function parseChatResponse(raw: string): ParsedChatResponse {
  const at = raw.indexOf(META_SENTINEL);
  if (at === -1) return { text: raw, meta: null };
  const text = raw.slice(0, at);
  try {
    return { text, meta: JSON.parse(raw.slice(at + 1)) as ChatMeta };
  } catch {
    return { text, meta: null };
  }
}
