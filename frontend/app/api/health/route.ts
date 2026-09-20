import { NextResponse } from "next/server";

// Liveness/readiness probe for the ALB target group and the ECS container
// health check. Deliberately dependency-free: /api/chat/health calls Pinecone,
// and a third-party outage must not take the whole service out of rotation.
export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json(
    { ok: true, uptime: Math.round(process.uptime()) },
    { headers: { "Cache-Control": "no-store" } },
  );
}
