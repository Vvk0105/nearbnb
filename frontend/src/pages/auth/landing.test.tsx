import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../../App";
import { AuthProvider } from "../../lib/auth";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const base = {
  phone: null, dob: null, photo_url: null, status: "active", email_verified: true, totp_enabled: false, pending_email: null,
  last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};
const PEOPLE: Record<string, { id: string; full_name: string; is_admin: boolean; host: string }> = {
  "guest@example.com": { id: "1", full_name: "Gail Guest", is_admin: false, host: "none" },
  "host@example.com": { id: "2", full_name: "Hannah Host", is_admin: false, host: "active" },
  "applicant@example.com": { id: "3", full_name: "Alex Applicant", is_admin: false, host: "pending_review" },
  "admin@example.com": { id: "4", full_name: "Ada Admin", is_admin: true, host: "none" },
};

let signedInAs: string | null = null;
function install() {
  signedInAs = null;
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const who = signedInAs ? PEOPLE[signedInAs] : null;
    const body = (email: string) => ({ ...base, ...PEOPLE[email], email });
    if (url.endsWith("/v1/me") && method === "GET") return who ? json(200, body(signedInAs!)) : json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    if (url.endsWith("/v1/auth/refresh")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    if (url.endsWith("/v1/auth/login")) {
      signedInAs = JSON.parse(String(init?.body)).email;
      return json(200, body(signedInAs!));
    }
    if (url.endsWith("/v1/host")) return who ? json(200, { state: who.host }) : json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    if (url.endsWith("/v1/admin/dashboard")) return json(200, { alerts: [], bookings: {}, money_30_days: {}, totals: {}, recent: [] });
    if (url.includes("/search")) return json(200, { total: 0, page: 1, page_size: 6, items: [], hint: null });
    if (url.includes("/notifications/unread-count")) return json(200, { unread: 0 });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
}

function open(path = "/login", state?: unknown) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[{ pathname: path, state }]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

async function logIn(email: string) {
  const u = userEvent.setup();
  await u.type(await screen.findByLabelText("Email"), email);
  await u.type(screen.getByLabelText("Password"), "whatever-password");
  await u.click(screen.getByRole("button", { name: "Log in" }));
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("where people land after logging in", () => {
  it("a guest lands on the home page", async () => {
    install(); open();
    await logIn("guest@example.com");
    expect(await screen.findByRole("heading", { level: 1, name: /Find a place/ })).toBeTruthy();
  });

  it("someone who only applied to host lands on the home page, not the host area", async () => {
    install(); open();
    await logIn("applicant@example.com");
    expect(await screen.findByRole("heading", { level: 1, name: /Find a place/ })).toBeTruthy();
  });

  it("a working host lands in the host area", async () => {
    install(); open();
    await logIn("host@example.com");
    await waitFor(() => expect(document.querySelector('nav[aria-label="Host"]')).toBeTruthy());
  });

  it("an admin lands in the admin area", async () => {
    install(); open();
    await logIn("admin@example.com");
    await waitFor(() => expect(document.querySelector('nav[aria-label="Admin"]')).toBeTruthy());
  });

  it("someone sent to log in from a page goes back to that page, whoever they are", async () => {
    install(); open("/login", { from: "/search?q=bondi" });
    await logIn("admin@example.com");
    expect(await screen.findByText(/Nothing matches yet/)).toBeTruthy();
    expect(document.querySelector('nav[aria-label="Admin"]')).toBeNull();
  });
});
