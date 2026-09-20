// Pinecone vector store for Setu (decision D7, 2026-08-04 — Dilip).
// Supersedes the in-repo JSON index as the PRIMARY retrieval path.
//
// Integrated index: Pinecone hosts the embedding model (llama-text-embed-v2),
// so we send plain text and it embeds server-side — no OpenAI/OpenRouter
// dependency. Metadata (tier/industry/url/...) rides on each record so
// filtering happens inside Pinecone rather than after retrieval.
//
// Ingest half only. Query-time search (semantic search + rerank, lexical
// fallback) moved to backend/app/services/retrieval.py with the chat route;
// this file keeps what scripts/pinecone-ingest.mts needs. The original is in
// _backup/webapp/lib/chat/pinecone.ts.

import type { ChatChunk } from "./index-build.ts";

export const INDEX_NAME = "saralprivacy-setu";
export const INDEX_HOST = "saralprivacy-setu-k0kthbf.svc.aped-4627-b74a.pinecone.io";
export const NAMESPACE = "content";
export const EMBED_MODEL = "llama-text-embed-v2";
export const RERANK_MODEL = "bge-reranker-v2-m3";
const API_VERSION = "2025-04";

/** Upsert cap for INTEGRATED indexes is 96 records/request (embedding batch). */
export const UPSERT_BATCH = 96;

export function pineconeKey(): string | null {
  const key = process.env.PINECONE_API_KEY?.trim();
  return key && key.length > 10 ? key : null;
}

export function isPineconeConfigured(): boolean {
  return pineconeKey() !== null;
}

/** Record shape sent to Pinecone. `chunk_text` is the embedded field (fieldMap);
 *  every other key is stored as filterable metadata. Values must be scalars or
 *  string arrays — no nested objects, no nulls. */
export interface PineconeRecord {
  _id: string;
  chunk_text: string;
  url: string;
  title: string;
  section: string;
  tier: number;
  extraction: string;
  topicTags: string[];
  industry?: string;
}

export function chunkToRecord(chunk: ChatChunk): PineconeRecord {
  const rec: PineconeRecord = {
    _id: chunk.id,
    // Prefix with title/section so the embedding carries page context, matching
    // how the lexical index weights those fields.
    chunk_text: `${chunk.title} — ${chunk.section}\n${chunk.text}`,
    url: chunk.url,
    title: chunk.title,
    section: chunk.section,
    tier: chunk.tier,
    extraction: chunk.extraction,
    topicTags: chunk.topicTags.slice(0, 20),
  };
  // Omit rather than null — Pinecone rejects null metadata values.
  if (chunk.industry) rec.industry = chunk.industry;
  return rec;
}

/** Upsert one batch (≤96 records). Throws on failure — ingest should be loud. */
export async function pineconeUpsert(records: PineconeRecord[]): Promise<void> {
  const key = pineconeKey();
  if (!key) throw new Error("PINECONE_API_KEY is not set");
  if (records.length > UPSERT_BATCH) {
    throw new Error(`batch too large: ${records.length} > ${UPSERT_BATCH}`);
  }
  // Integrated upsert takes NDJSON, one record per line.
  const body = records.map((r) => JSON.stringify(r)).join("\n");
  const res = await fetch(`https://${INDEX_HOST}/records/namespaces/${NAMESPACE}/upsert`, {
    method: "POST",
    headers: {
      "Api-Key": key,
      "Content-Type": "application/x-ndjson",
      "X-Pinecone-API-Version": API_VERSION,
    },
    body,
  });
  if (!res.ok) {
    throw new Error(`pinecone upsert ${res.status}: ${(await res.text()).slice(0, 300)}`);
  }
}

export async function pineconeStats(): Promise<{ vectorCount: number } | null> {
  const key = pineconeKey();
  if (!key) return null;
  try {
    const res = await fetch(`https://${INDEX_HOST}/describe_index_stats`, {
      method: "POST",
      headers: {
        "Api-Key": key,
        "Content-Type": "application/json",
        "X-Pinecone-API-Version": API_VERSION,
      },
      body: "{}",
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      namespaces?: Record<string, { recordCount?: number; vectorCount?: number }>;
      totalVectorCount?: number;
    };
    const ns = data.namespaces?.[NAMESPACE];
    return { vectorCount: ns?.recordCount ?? ns?.vectorCount ?? data.totalVectorCount ?? 0 };
  } catch {
    return null;
  }
}
