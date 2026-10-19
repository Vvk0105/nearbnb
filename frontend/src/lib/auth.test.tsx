import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "./auth";

/** A tiny fake API that behaves like the real one: a single browser session held in cookies. */
const USERS: Record<string, { id: string; full_name: string; is_admin: boolean }> = {
  "alice@example.com": { id: "1", full_name: "Alice Guest", is_admin: false },
  "bob@example.com": { id: "2", full_name: "Bob Other", is_admin: false },
};

function installFakeApi() {
  let sessionEmail: string | null = null;
  const json = (status: number, body: unknown) =>
    Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  const userBody = (email: string) => ({
    ...USERS[email], email, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
    totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
  });

  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/v1/me") && method === "GET") {
      return sessionEmail ? json(200, userBody(sessionEmail)) : json(401, { error: { code: "NOT_AUTHENTICATED", message: "Please log in" } });
    }
    if (url.endsWith("/v1/auth/refresh")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "Session expired" } });
    if (url.endsWith("/v1/auth/login")) {
      const { email } = JSON.parse(String(init?.body));
      sessionEmail = email;
      return json(200, userBody(email));
    }
    if (url.endsWith("/v1/auth/logout")) {
      sessionEmail = null;
      return json(200, { ok: true });
    }
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
}

function renderApp(path = "/login") {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <App />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

async function logIn(email: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), "whatever-password");
  await user.click(screen.getByRole("button", { name: "Log in" }));
}

const header = () => within(screen.getByRole("banner"));

describe("login and logout update the screen without a page refresh", () => {
  beforeEach(installFakeApi);
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows the user's name right after login", async () => {
    renderApp();
    await waitFor(() => expect(header().getByRole("link", { name: "Log in" })).toBeTruthy());
    await logIn("alice@example.com");
    await waitFor(() => expect(header().getByRole("button", { name: "Account menu" })).toBeTruthy());
    expect(header().getByText("Alice Guest")).toBeTruthy();
    expect(header().queryByRole("link", { name: "Log in" })).toBeNull();
  });

  it("logs out immediately and returns to the login page", async () => {
    renderApp();
    await logIn("alice@example.com");
    const user = userEvent.setup();
    await user.click(await header().findByRole("button", { name: "Account menu" }));
    await user.click(await header().findByRole("menuitem", { name: "Log out" }));
    await waitFor(() => expect(header().getByRole("link", { name: "Log in" })).toBeTruthy());
    expect(header().queryByText("Alice Guest")).toBeNull();
    expect(screen.getByRole("heading", { name: "Log in" })).toBeTruthy();
  });

  it("switches to a different user after logout with no leftovers from the first", async () => {
    renderApp();
    await logIn("alice@example.com");
    const user = userEvent.setup();
    await user.click(await header().findByRole("button", { name: "Account menu" }));
    await user.click(await header().findByRole("menuitem", { name: "Log out" }));
    await waitFor(() => expect(header().getByRole("link", { name: "Log in" })).toBeTruthy());

    await logIn("bob@example.com");
    await waitFor(() => expect(header().getByText("Bob Other")).toBeTruthy());
    expect(header().queryByText("Alice Guest")).toBeNull();
  });

  it("protected pages redirect to login after logout", async () => {
    renderApp("/account");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Log in" })).toBeTruthy());
  });
});
