import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "ann@example.com", full_name: "Ann Guest", is_admin: false, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};

let deleteReply: () => Promise<Response> = () => json(200, { ok: true });
let sent: { url: string; body: unknown }[] = [];

function open(path: string, { signedIn = true } = {}) {
  sent = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (init?.method === "POST") sent.push({ url, body: init.body ? JSON.parse(String(init.body)) : null });
    if (url.endsWith("/v1/me")) return signedIn ? json(200, me) : json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    if (url.endsWith("/v1/me/delete")) return deleteReply();
    if (url.endsWith("/v1/auth/sessions")) return json(200, []);
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); deleteReply = () => json(200, { ok: true }); });

describe("deleting an account on the website", () => {
  it("asks for the password, says what is erased and kept, and deletes", async () => {
    open("/account/security");
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Delete my account" }));
    expect(screen.getByText(/cannot be undone/)).toBeTruthy();
    expect(screen.getByText(/Past bookings and payments are kept without your details/)).toBeTruthy();
    await u.type(screen.getByLabelText("Confirm with your password"), "my-password");
    await u.click(screen.getByRole("button", { name: "Delete my account" }));
    await waitFor(() => expect(sent.some((s) => s.url.endsWith("/v1/me/delete"))).toBe(true));
    expect(sent.find((s) => s.url.endsWith("/v1/me/delete"))!.body).toEqual({ password: "my-password" });
  });

  it("explains why when a booking in progress blocks it", async () => {
    deleteReply = () => json(409, { error: { code: "OPEN_BOOKINGS", message: "You have bookings in progress. Cancel them, or wait until your stays are over, then try again." } });
    open("/account/security");
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Delete my account" }));
    await u.type(screen.getByLabelText("Confirm with your password"), "my-password");
    await u.click(screen.getByRole("button", { name: "Delete my account" }));
    expect(await screen.findByText(/You have bookings in progress/)).toBeTruthy();
  });
});

describe("the public page Google Play links to", () => {
  it("works without signing in, explains the steps and what happens to the data", async () => {
    open("/delete-account", { signedIn: false });
    expect(await screen.findByRole("heading", { level: 1, name: "Delete your NearBnB account" })).toBeTruthy();
    for (const h of ["How to delete it", "What is deleted", "What is kept", "Before you delete"]) {
      expect(screen.getByRole("heading", { level: 2, name: h })).toBeTruthy();
    }
    expect(screen.getByRole("link", { name: "Sign in to delete your account" }).getAttribute("href")).toBe("/login");
  });

  it("sends someone signed in straight to the setting, and the footer links to the page", async () => {
    open("/delete-account");
    expect((await screen.findByRole("link", { name: "Go to Security settings" })).getAttribute("href")).toBe("/account/security");
    expect(screen.getByRole("link", { name: "Delete your account" }).getAttribute("href")).toBe("/delete-account");
  });
});
