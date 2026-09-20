/**
 * Server-side data access for server components and route handlers.
 * Never import from client components — BACKEND_URL is a server-only address.
 *
 *   const posts = await apiGet<Post[]>("/editorial/blog", { tags: ["blog-posts"], revalidate: 3600 });
 */
import "server-only";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";
const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown, message?: string) {
    super(message ?? `API ${status}`);
  }
}

interface Opts {
  tags?: string[];
  revalidate?: number | false;
  headers?: Record<string, string>;
}

export function apiUrl(path: string): string {
  return `${BACKEND_URL}${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  let body: unknown = text;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) throw new ApiError(res.status, body);
  return body as T;
}

export async function apiGet<T>(path: string, opts: Opts = {}): Promise<T> {
  const res = await fetch(apiUrl(path), {
    headers: { accept: "application/json", ...(opts.headers ?? {}) },
    next: { tags: opts.tags, revalidate: opts.revalidate },
  });
  return parse<T>(res);
}

export async function apiPost<T>(path: string, json: unknown, opts: Opts = {}): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json", ...(opts.headers ?? {}) },
    body: JSON.stringify(json),
    cache: "no-store",
  });
  return parse<T>(res);
}

/** Server-to-server calls that must authenticate as the backend (cron secret). */
export function cronHeaders(): Record<string, string> {
  return { authorization: `Bearer ${process.env.CRON_SECRET ?? ""}` };
}
