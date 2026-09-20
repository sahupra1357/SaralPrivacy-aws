import type { Metadata } from "next";
import { cookies } from "next/headers";
import { verifyAccessToken, ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";
import BlogEditor from "@/components/admin/BlogEditor";

export const metadata: Metadata = { title: "New Blog Post | Admin" };

export default async function NewBlogPostPage() {
  const cookieStore = await cookies();
  const session = await verifyAccessToken(cookieStore.get(ACCESS_TOKEN_COOKIE)?.value);
  const role: "admin" | "blogger" = session?.role === "blogger" ? "blogger" : "admin";

  return <BlogEditor docId={null} role={role} />;
}
