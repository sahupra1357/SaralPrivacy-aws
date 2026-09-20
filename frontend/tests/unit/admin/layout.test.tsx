/** Admin layout: sidebar by role, verified against the access_token cookie. */
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (n: string) => (n === "access_token" ? { name: n, value: "jwt" } : undefined) }),
}));
vi.mock("@/lib/auth/session", () => ({ ACCESS_TOKEN_COOKIE: "access_token", verifyAccessToken: vi.fn() }));
vi.mock("@/app/(backoffice)/admin/_lib/signOut", () => ({ signOut: vi.fn() }));

import { verifyAccessToken } from "@/lib/auth/session";
import AdminLayout from "@/app/(backoffice)/admin/layout";

describe("admin layout", () => {
  beforeEach(() => vi.mocked(verifyAccessToken).mockReset());

  it("shows the full admin nav and Sign Out for an admin", async () => {
    vi.mocked(verifyAccessToken).mockResolvedValue({ role: "admin", name: "Dilip" });
    render(await AdminLayout({ children: <p>child</p> }));

    expect(verifyAccessToken).toHaveBeenCalledWith("jwt");
    expect(screen.getByText("Admin Dashboard")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /SEO Watcher/ })).toHaveAttribute("href", "/admin/seo");
    expect(screen.getByRole("link", { name: /Bloggers/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Sign Out/ })).toBeInTheDocument();
    expect(screen.getByText("Dilip")).toBeInTheDocument();
  });

  it("limits a blogger to the non-admin items", async () => {
    vi.mocked(verifyAccessToken).mockResolvedValue({ role: "blogger" });
    render(await AdminLayout({ children: <p>child</p> }));

    expect(screen.getByText("Blog Contributor")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Blog Posts/ })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Bloggers/ })).not.toBeInTheDocument();
  });

  it("renders children without the sidebar when there is no session", async () => {
    vi.mocked(verifyAccessToken).mockResolvedValue(null);
    render(await AdminLayout({ children: <p>login form</p> }));
    expect(screen.getByText("login form")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Sign Out/ })).not.toBeInTheDocument();
  });
});
