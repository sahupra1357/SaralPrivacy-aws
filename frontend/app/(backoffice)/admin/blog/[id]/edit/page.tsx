import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cookies } from "next/headers";
import { verifyAccessToken, ACCESS_TOKEN_COOKIE } from "@/lib/auth/session";
import { apiGet } from "@/lib/api";
import BlogEditor, { type BlogPostData, type PrimarySource } from "@/components/admin/BlogEditor";

export const metadata: Metadata = { title: "Edit Blog Post | Admin" };

interface Props {
  params: Promise<{ id: string }>;
}

function tryParse<T>(str: string | null | undefined, fallback: T): T {
  if (!str) return fallback;
  try { return JSON.parse(str) as T; } catch { return fallback; }
}

// A blog_posts row as untyped JSON from GET /blog/{id}; read field by field below.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type BlogDoc = Record<string, any>;

export default async function EditBlogPostPage({ params }: Props) {
  const { id } = await params;

  // Resolve role for role-gated UI in editor — signature-verified
  const cookieStore = await cookies();
  const token = cookieStore.get(ACCESS_TOKEN_COOKIE)?.value;
  const session = await verifyAccessToken(token);
  const role: "admin" | "blogger" = session?.role === "blogger" ? "blogger" : "admin";

  // Server-to-backend read with the editor's own session (drafts are editor-only).
  let doc: BlogDoc;
  try {
    doc = await apiGet<BlogDoc>(`/blog/${encodeURIComponent(id)}`, {
      revalidate: 0,
      headers: token ? { authorization: `Bearer ${token}` } : {},
    });
  } catch {
    notFound();
  }

  // Parse the packed sections_json field if present
  const sectionsJson = tryParse<Record<string, string>>(doc.sections_json, {});

  const initialData: Partial<BlogPostData> = {
    title:                doc.title                || "",
    slug:                 doc.slug                 || "",
    excerpt:              doc.excerpt              || "",
    lane:                 doc.lane                 || "law-explained",
    author:               doc.author               || "",
    tags:                 doc.tags                 || "",
    featured:             doc.featured             ?? false,
    section_what_changed: doc.section_what_changed || "",
    section_law_says:     doc.section_law_says     || "",
    section_do_now:       sectionsJson.section_do_now       || "",
    section_uncertain:    sectionsJson.section_uncertain    || "",
    section_mistakes:     sectionsJson.section_mistakes     || "",
    primary_sources:      tryParse<PrimarySource[]>(sectionsJson.primary_sources, []),
    validated_at:         doc.validated_at         || "",
    score_legal_accuracy: doc.score_legal_accuracy ?? 0,
    score_primary_source: doc.score_primary_source ?? 0,
    score_currency:       doc.score_currency       ?? 0,
    score_scope:          doc.score_scope          ?? 0,
    score_operational:    doc.score_operational    ?? 0,
  };

  return <BlogEditor initialData={initialData} docId={id} role={role} initialStatus={doc.status} />;
}
