/**
 * Long admin runs (AEO panel, SEO inspection) now run as backend background tasks: the
 * POST answers 202 with a task id and the "Run now" buttons poll it until it lands.
 * The task's `result` is exactly the JSON the old synchronous route returned.
 */
export const ADMIN_API = "/api/proxy/api/v1/admin";

export type TaskOutcome<T> =
  | { status: "done" | "failed"; result: T | null }
  | { status: "unauthorized" | "timeout"; result: null };

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export async function pollTask<T>(
  taskId: string,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<TaskOutcome<T>> {
  const intervalMs = opts.intervalMs ?? 3000;
  const timeoutMs = opts.timeoutMs ?? 330_000; // the old 300 s ceiling plus slack
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    await sleep(intervalMs);
    let res: Response;
    try {
      res = await fetch(`${ADMIN_API}/tasks/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    } catch {
      continue; // a network blip while waiting is not a failed run
    }
    if (res.status === 401) return { status: "unauthorized", result: null };
    if (!res.ok) continue;
    const task = (await res.json().catch(() => null)) as { status?: string; result?: T | null } | null;
    if (task && (task.status === "done" || task.status === "failed")) {
      return { status: task.status, result: task.result ?? null };
    }
  }
  return { status: "timeout", result: null };
}
