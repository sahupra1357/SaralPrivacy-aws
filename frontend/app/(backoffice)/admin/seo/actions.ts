"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { verifyAccessToken, ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";
import { adminPost } from "../_lib/backend";

/**
 * "Mark requested" — records that the GSC Request Indexing button was pressed
 * for a URL, so the watcher never shortlists it again (quota rule). The DB
 * ledger (ops.seo_index_requests) merges with the code ledger in the backend.
 */
export async function markRequested(formData: FormData): Promise<void> {
  const session = await verifyAccessToken((await cookies()).get(ACCESS_TOKEN_COOKIE)?.value);
  if (!session || session.role !== "admin") throw new Error("Unauthorized");

  const url = String(formData.get("url") || "").trim();
  if (!/^https:\/\/saralprivacy\.com\//.test(url)) throw new Error("URL must be on https://saralprivacy.com/");

  await adminPost("/admin/seo/index-requests", { url });
  revalidatePath("/admin/seo");
}
