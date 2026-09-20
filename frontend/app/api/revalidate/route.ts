import { NextRequest, NextResponse } from "next/server";
import { revalidatePath, revalidateTag } from "next/cache";

/**
 * The backend busts ISR through this route (backend/app/services/revalidate.py).
 *
 * POST /api/revalidate   Authorization: Bearer <CRON_SECRET>   body {tag} | {path}
 *   Used after every blog save / infographic / briefing publish.
 *
 * GET /api/revalidate?secret=<CRON_SECRET>  (or x-revalidate-secret header)
 *   Legacy form: clears the "briefings" tag. Kept for any external caller.
 */

function expectedSecret(): string {
  return (process.env.CRON_SECRET || process.env.BRIEFING_CRON_SECRET || "").trim();
}

export async function GET(request: NextRequest) {
  // Prefer the header — query strings land in access logs and referrers.
  const secret =
    (request.headers.get("x-revalidate-secret") || "").trim() ||
    request.nextUrl.searchParams.get("secret") || "";
  const expected = expectedSecret();

  if (!expected || secret !== expected) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }

  revalidateTag("briefings", "default");

  return NextResponse.json({ revalidated: true, tag: "briefings", at: new Date().toISOString() });
}

export async function POST(request: NextRequest) {
  const secret = (request.headers.get("authorization") || "").replace("Bearer ", "").trim();
  const expected = expectedSecret();

  if (!expected || secret !== expected) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }

  let body: { tag?: unknown; path?: unknown; type?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  const at = new Date().toISOString();
  if (typeof body.tag === "string" && body.tag) {
    revalidateTag(body.tag, "default");
    return NextResponse.json({ revalidated: true, tag: body.tag, at });
  }
  if (typeof body.path === "string" && body.path.startsWith("/")) {
    // type "layout" refreshes every page under the path (used by the container's
    // start-up warm-up to replace pages that were pre-built without a backend).
    if (body.type === "layout") {
      revalidatePath(body.path, "layout");
      return NextResponse.json({ revalidated: true, path: body.path, type: "layout", at });
    }
    revalidatePath(body.path);
    return NextResponse.json({ revalidated: true, path: body.path, at });
  }
  return NextResponse.json({ error: "tag or path is required." }, { status: 400 });
}
