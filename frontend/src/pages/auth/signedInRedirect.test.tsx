import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../../App";
import { AuthProvider } from "../../lib/auth";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "ann@example.com", full_name: "Ann Guest", is_admin: false, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};

function open(path: string, meReply: () => Promise<Response>) {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/v1/me")) return meReply();
    if (url.includes("/search")) return json(200, { total: 0, page: 1, page_size: 6, items: [], hint: null });
    if (url.includes("/notifications/unread-count")) return json(200, { unread: 0 });
    if (url.endsWith("/v1/host")) return json(200, { state: "none" });
    return json(404, { error: { code: "NOT_FOUND", message: "x" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("someone already signed in", () => {
  it("is not shown the login form", async () => {
    open("/login", () => json(200, me));
    expect(await screen.findByRole("heading", { level: 1, name: /Find a place/ })).toBeTruthy();    // sent on to the home page
    expect(screen.queryByRole("heading", { name: "Log in" })).toBeNull();
  });

  it("is not shown the sign-up form", async () => {
    open("/register", () => json(200, me));
    expect(await screen.findByRole("heading", { level: 1, name: /Find a place/ })).toBeTruthy();
    expect(screen.queryByLabelText("Full name")).toBeNull();
  });
});

describe("the header while the site checks who you are", () => {
  it("shows no Log in link until it knows, then the right one", async () => {
    let answer: (r: Response) => void = () => undefined;
    open("/", () => new Promise<Response>((resolve) => { answer = resolve; }));
    expect(screen.queryByRole("link", { name: "Log in" })).toBeNull();          // still checking: nothing misleading
    answer(new Response(JSON.stringify({ error: { code: "NOT_AUTHENTICATED", message: "x" } }), { status: 401, headers: { "Content-Type": "application/json" } }));
    expect(await screen.findByRole("link", { name: "Log in" })).toBeTruthy();    // signed out: now the link appears
  });
});
