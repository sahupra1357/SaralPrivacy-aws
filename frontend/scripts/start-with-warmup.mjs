#!/usr/bin/env node
/**
 * Container entrypoint: start the Next.js standalone server, then refresh every page
 * that `next build` pre-rendered without a backend (Docker builds cannot reach it, so
 * those pages would otherwise show empty lists until their ISR window expires).
 *
 * The refresh goes through the app's own POST /api/revalidate with CRON_SECRET. A
 * failed warm-up is logged and ignored: the site still serves, and ISR catches up.
 */
import { spawn } from "node:child_process";

const port = process.env.PORT || "3000";
const base = `http://127.0.0.1:${port}`;
const secret = (process.env.CRON_SECRET || process.env.BRIEFING_CRON_SECRET || "").trim();

const server = spawn(process.execPath, ["server.js"], { stdio: "inherit", env: process.env });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => server.kill(sig));
server.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));

async function waitHealthy(timeoutMs = 60_000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    try {
      if ((await fetch(`${base}/api/health`)).ok) return true;
    } catch {
      /* not listening yet */
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
}

async function refresh(body) {
  const res = await fetch(`${base}/api/revalidate`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${secret}` },
    body: JSON.stringify(body),
  });
  return `${JSON.stringify(body)} -> ${res.status}`;
}

(async () => {
  if (!secret) return console.warn("[warmup] CRON_SECRET unset; skipping page refresh");
  if (!(await waitHealthy())) return console.warn("[warmup] server not healthy in 60s; skipping");
  try {
    const results = [];
    for (const body of [{ path: "/", type: "layout" }, { tag: "briefings" }, { tag: "blog-posts" }]) {
      results.push(await refresh(body));
    }
    console.log(`[warmup] refreshed pre-built pages: ${results.join("; ")}`);
  } catch (err) {
    console.warn("[warmup] refresh failed (site still serving):", err?.message || err);
  }
})();
