import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SetPasswordPage from "@/app/(backoffice)/admin/set-password/page";

const searchParams = { value: new URLSearchParams() };

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => searchParams.value,
  usePathname: () => "/admin/set-password",
}));

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function fill(password: string, confirm = password) {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("Minimum 12 characters"), password);
  await user.type(screen.getByPlaceholderText("Re-enter your password"), confirm);
  return user;
}

describe("/admin/set-password", () => {
  beforeEach(() => {
    searchParams.value = new URLSearchParams("token=tok-1&type=invite");
  });

  it("renders the invite copy for type=invite", async () => {
    render(<SetPasswordPage />);

    expect(await screen.findByText("Set your password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Activate My Account" })).toBeInTheDocument();
  });

  it("renders the reset copy for type=recovery", async () => {
    searchParams.value = new URLSearchParams("token=tok-1&type=recovery");
    render(<SetPasswordPage />);

    expect(await screen.findByText("Choose a new password")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update Password" })).toBeInTheDocument();
  });

  it("posts token, type and password to /api/auth/set-password and confirms", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse(200, { success: true }));
    render(<SetPasswordPage />);
    await screen.findByText("Set your password");

    const user = await fill("a-very-long-password");
    await user.click(screen.getByRole("button", { name: "Activate My Account" }));

    await waitFor(() => expect(screen.getByText("Account activated!")).toBeInTheDocument());
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/set-password");
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({
      token: "tok-1",
      type: "invite",
      password: "a-very-long-password",
    });
  });

  it("still reads a legacy token_hash link", async () => {
    searchParams.value = new URLSearchParams("token_hash=old-1&type=recovery");
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse(200, { success: true }));
    render(<SetPasswordPage />);
    await screen.findByText("Choose a new password");

    const user = await fill("a-very-long-password");
    await user.click(screen.getByRole("button", { name: "Update Password" }));

    await waitFor(() =>
      expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string).token).toBe("old-1"),
    );
  });

  it("refuses a short password client-side without calling the API", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    render(<SetPasswordPage />);
    await screen.findByText("Set your password");

    const user = await fill("short");
    await user.click(screen.getByRole("button", { name: "Activate My Account" }));

    await waitFor(() =>
      expect(screen.getByText("Password must be at least 12 characters.")).toBeInTheDocument(),
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a mismatched confirmation", async () => {
    const fetchMock = vi.spyOn(global, "fetch");
    render(<SetPasswordPage />);
    await screen.findByText("Set your password");

    const user = await fill("a-very-long-password", "a-different-password");
    await user.click(screen.getByRole("button", { name: "Activate My Account" }));

    await waitFor(() => expect(screen.getByText("Passwords do not match.")).toBeInTheDocument());
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the backend's expired-link message", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      jsonResponse(401, { error: "This link is invalid or has expired. Ask the admin for a new one." }),
    );
    render(<SetPasswordPage />);
    await screen.findByText("Set your password");

    const user = await fill("a-very-long-password");
    await user.click(screen.getByRole("button", { name: "Activate My Account" }));

    await waitFor(() =>
      expect(
        screen.getByText("This link is invalid or has expired. Ask the admin for a new one."),
      ).toBeInTheDocument(),
    );
  });

  it("warns when the link carries no token at all", async () => {
    searchParams.value = new URLSearchParams("type=invite");
    render(<SetPasswordPage />);

    await waitFor(() =>
      expect(
        screen.getByText("Invalid link. Please contact the admin for a new one."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Activate My Account" })).toBeDisabled();
  });
});
