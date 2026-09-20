import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import AdminLogin from "@/app/(backoffice)/admin/login/page";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function submitPassword(email = "a@b.c", password = "secret-password") {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText("you@example.com"), email);
  await user.type(screen.getByPlaceholderText("Enter your password"), password);
  await user.click(screen.getByRole("button", { name: "Continue" }));
  return user;
}

describe("/admin/login", () => {
  it("renders the password step first", () => {
    render(<AdminLogin />);

    expect(screen.getByPlaceholderText("you@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.queryByText("Two-step verification")).not.toBeInTheDocument();
  });

  it("posts to /api/auth/login and shows the code screen for a returning user", async () => {
    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValue(jsonResponse(200, { success: true, step: "verify", role: "admin" }));
    render(<AdminLogin />);

    await submitPassword();

    await waitFor(() => expect(screen.getByText("Two-step verification")).toBeInTheDocument());
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/login");
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string)).toEqual({
      email: "a@b.c",
      password: "secret-password",
    });
  });

  it("starts enrollment via /api/auth/mfa/enroll and shows the QR code", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/auth/login") {
        return jsonResponse(200, { success: true, step: "enroll", role: "admin" });
      }
      return jsonResponse(200, {
        factorId: "u-1",
        qr: "data:image/svg+xml;base64,PHN2Zz4=",
        secret: "JBSWY3DPEHPK3PXP",
      });
    });
    render(<AdminLogin />);

    await submitPassword();

    await waitFor(() =>
      expect(screen.getByText("Set up your verification app")).toBeInTheDocument(),
    );
    expect(screen.getByAltText("QR code for your authenticator app")).toHaveAttribute(
      "src",
      "data:image/svg+xml;base64,PHN2Zz4=",
    );
    expect(screen.getByText("JBSWY3DPEHPK3PXP")).toBeInTheDocument();
  });

  it("shows the backend's exact error on a bad password", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse(401, { error: "Invalid credentials." }));
    render(<AdminLogin />);

    await submitPassword("a@b.c", "wrong-password");

    await waitFor(() => expect(screen.getByText("Invalid credentials.")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });

  it("shows Access denied. for a revoked blogger", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse(403, { error: "Access denied." }));
    render(<AdminLogin />);

    await submitPassword();

    await waitFor(() => expect(screen.getByText("Access denied.")).toBeInTheDocument());
  });

  it("posts the code to /api/auth/mfa/verify and shows a mismatch message", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/auth/login") {
        return jsonResponse(200, { success: true, step: "verify", role: "admin" });
      }
      return jsonResponse(401, { error: "Code did not match. Try again." });
    });
    render(<AdminLogin />);
    const user = await submitPassword();
    await waitFor(() => expect(screen.getByText("Two-step verification")).toBeInTheDocument());

    await user.type(screen.getByPlaceholderText("000000"), "123456");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() =>
      expect(screen.getByText("Code did not match. Try again.")).toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls.at(-1)![0]).toBe("/api/auth/mfa/verify");
  });

  it("returns to the password screen when the backend says step:login", async () => {
    vi.spyOn(global, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/auth/login") {
        return jsonResponse(200, { success: true, step: "verify", role: "admin" });
      }
      return jsonResponse(401, { error: "Session expired. Please sign in again.", step: "login" });
    });
    render(<AdminLogin />);
    const user = await submitPassword();
    await waitFor(() => expect(screen.getByText("Two-step verification")).toBeInTheDocument());

    await user.type(screen.getByPlaceholderText("000000"), "123456");
    await user.click(screen.getByRole("button", { name: "Sign In" }));

    await waitFor(() =>
      expect(screen.getByText("Session expired. Please sign in again.")).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });
});
