import type { Metadata } from "next";

import { adminGet } from "../_lib/backend";
import BloggersClient from "./BloggersClient";

export const metadata: Metadata = { title: "Blogger Management | Admin" };
export const dynamic = "force-dynamic";

async function fetchBloggers() {
  try {
    const result = await adminGet<{ bloggers: Record<string, unknown>[] }>("/admin/bloggers");
    return result.bloggers ?? [];
  } catch {
    return [];
  }
}

export default async function BloggersPage() {
  const bloggers = await fetchBloggers();
  return <BloggersClient initialBloggers={bloggers} />;
}
