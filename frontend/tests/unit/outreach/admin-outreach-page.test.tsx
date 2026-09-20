import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OutreachPage from "@/app/(backoffice)/admin/outreach/page";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const STATS = { total: 3, pending: 1, sent: 1, subscribed: 1, bounced: 0, unsubscribed: 0, complained: 0 };
const CONTACTS = {
  documents: [
    { $id: "1", email: "asha@example.com", name: "Asha Rao", company: "Acme", status: "sent" },
    { $id: "2", email: "ravi@example.com", status: "pending" },
  ],
  total: 2,
};

function routeFetch(importBody: unknown = { success: true, total: 2, inserted: 2, duplicates: 0, invalid: 0 }, importStatus = 200) {
  return vi.spyOn(global, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.endsWith("/outreach/stats")) return jsonResponse(STATS);
    if (url.includes("/outreach/contacts")) return jsonResponse(CONTACTS);
    if (url.endsWith("/outreach/import")) return jsonResponse(importBody, importStatus);
    throw new Error(`unexpected fetch ${url}`);
  });
}

function upload(container: HTMLElement) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement;
  const file = new File(["Email\na@b.co\n"], "list.csv", { type: "text/csv" });
  fireEvent.change(input, { target: { files: [file] } });
}

describe("admin /outreach", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("loads stats and contacts from the outreach endpoints", async () => {
    const spy = routeFetch();
    render(<OutreachPage />);

    expect(await screen.findByText("asha@example.com")).toBeInTheDocument();
    const urls = spy.mock.calls.map(([u]) => String(u));
    expect(urls).toContain("/api/proxy/api/v1/outreach/stats");
    expect(urls).toContain("/api/proxy/api/v1/outreach/contacts?limit=200");
  });

  it("filters contacts by status through the query string", async () => {
    const spy = routeFetch();
    render(<OutreachPage />);
    await screen.findByText("asha@example.com");

    fireEvent.click(screen.getByRole("button", { name: "pending" }));

    await waitFor(() =>
      expect(spy.mock.calls.map(([u]) => String(u))).toContain(
        "/api/proxy/api/v1/outreach/contacts?limit=200&status=pending",
      ),
    );
  });

  it("uploads the CSV through the proxy and shows the summary", async () => {
    const spy = routeFetch();
    const { container } = render(<OutreachPage />);
    await screen.findByText("asha@example.com");

    upload(container);

    expect(await screen.findByText(/Import complete/)).toBeInTheDocument();
    const call = spy.mock.calls.find(([u]) => String(u).endsWith("/outreach/import"));
    expect(call?.[0]).toBe("/api/proxy/api/v1/outreach/import");
    expect((call?.[1] as RequestInit).body).toBeInstanceOf(FormData);
  });

  it("shows the backend's error detail when the import is rejected", async () => {
    routeFetch({ detail: "Spreadsheet is empty." }, 400);
    const { container } = render(<OutreachPage />);
    await screen.findByText("asha@example.com");

    upload(container);

    expect(await screen.findByText("Spreadsheet is empty.")).toBeInTheDocument();
  });
});
