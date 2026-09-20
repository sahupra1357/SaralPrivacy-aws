/** BloggersClient — invite / revoke / delete now go to /api/v1/admin/bloggers via the proxy. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BloggersClient from "@/app/(backoffice)/admin/bloggers/BloggersClient";

const API = "/api/proxy/api/v1/admin/bloggers";
const active = {
  $id: "b1",
  email: "asha@example.com",
  name: "Asha",
  bio: "",
  active: true,
  invite_token: "",
  created_at: "2026-09-01T10:00:00.000Z",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("BloggersClient", () => {
  beforeEach(() => {
    vi.spyOn(global, "fetch");
  });

  it("invites through the backend and shows the invite link", async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(json({ success: true, id: "b2", inviteUrl: "https://x/set-password?token=t&type=invite", emailSent: true, emailError: null }))
      .mockResolvedValueOnce(json({ bloggers: [active] }));
    render(<BloggersClient initialBloggers={[]} />);

    await userEvent.click(screen.getByRole("button", { name: /Invite Blogger/ }));
    await userEvent.type(screen.getByPlaceholderText("e.g. Priya Sharma"), "Priya");
    await userEvent.type(screen.getByPlaceholderText("e.g. priya@example.com"), "priya@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send Invite" }));

    await waitFor(() => expect(global.fetch).toHaveBeenCalledTimes(2));
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe(API);
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((init as RequestInit).body))).toMatchObject({ name: "Priya", email: "priya@example.com" });
    expect(vi.mocked(global.fetch).mock.calls[1][0]).toBe(API);
    expect(await screen.findByText("asha@example.com")).toBeInTheDocument();
  });

  it("shows the backend's error message (FastAPI `detail`)", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(json({ detail: "A blogger with this email already exists." }, 409));
    render(<BloggersClient initialBloggers={[]} />);

    await userEvent.click(screen.getByRole("button", { name: /Invite Blogger/ }));
    await userEvent.type(screen.getByPlaceholderText("e.g. Priya Sharma"), "Priya");
    await userEvent.type(screen.getByPlaceholderText("e.g. priya@example.com"), "priya@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Send Invite" }));

    expect(await screen.findByText("A blogger with this email already exists.")).toBeInTheDocument();
  });

  it("revokes access with a PATCH to the blogger", async () => {
    vi.mocked(global.fetch).mockResolvedValue(json({ success: true }));
    render(<BloggersClient initialBloggers={[active]} />);

    fireEvent.click(screen.getByTitle("Revoke access"));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [url, init] = vi.mocked(global.fetch).mock.calls[0];
    expect(url).toBe(`${API}/b1`);
    expect((init as RequestInit).method).toBe("PATCH");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ active: false });
    expect(await screen.findByTitle("Restore access")).toBeInTheDocument();
  });

  it("deletes after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(global.fetch).mockResolvedValue(json({ success: true }));
    render(<BloggersClient initialBloggers={[active]} />);

    fireEvent.click(screen.getByTitle("Delete permanently"));

    await waitFor(() => expect(screen.queryByText("asha@example.com")).not.toBeInTheDocument());
    expect(vi.mocked(global.fetch).mock.calls[0]).toEqual([`${API}/b1`, { method: "DELETE" }]);
  });
});
