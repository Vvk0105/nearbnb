import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { TripProgress } from "./account/Trips";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "ann@example.com", full_name: "Ann Guest", is_admin: false, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};

function open(path: string) {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/v1/me")) return json(200, me);
    if (url.endsWith("/v1/host")) return json(200, { state: "none" });
    if (url.endsWith("/v1/auth/sessions")) return json(200, []);
    if (url.includes("/v1/me/bookings")) return json(200, { items: [], total: 0, page: 1, page_size: 20, counts: { pending: 0, awaiting_payment: 0, upcoming: 0, past: 0 } });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("account settings and trips", () => {
  it("has a side menu with only profile and security, and opens on the profile", async () => {
    open("/account");
    const nav = await screen.findByRole("navigation", { name: "Account settings" });
    expect(within(nav).getAllByRole("link").map((l) => l.textContent?.trim())).toEqual(["Profile", "Security"]);
    expect(await screen.findByRole("heading", { level: 1, name: "Profile" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Change password" })).toBeNull();   // that lives under Security now
  });

  it("keeps password, email, devices and closing the account together under Security", async () => {
    open("/account/security");
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeTruthy();
    for (const title of ["Change password", "Change email", "Active sessions", "Deactivate account"]) {
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeTruthy();
    }
  });

  it("shows trips on their own page, without the settings menu, with a friendly empty state", async () => {
    open("/account/trips");
    expect(await screen.findByRole("heading", { level: 1, name: "My trips" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Account settings" })).toBeNull();
    expect(await screen.findByText("No trips here yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Find a place to stay" }).getAttribute("href")).toBe("/search");
  });

  it("moves between sections from the side menu", async () => {
    const u = userEvent.setup();
    open("/account");
    const nav = await screen.findByRole("navigation", { name: "Account settings" });
    await u.click(within(nav).getByRole("link", { name: "Security" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Security" })).toBeTruthy();
  });
});

describe("trip progress", () => {
  const states = (status: Parameters<typeof TripProgress>[0]["status"]) => {
    const { container, unmount } = render(<TripProgress status={status} />);
    const current = container.querySelector("[aria-current='step']")?.parentElement?.textContent ?? null;
    const doneSteps = container.querySelectorAll("svg").length;               // a tick for each step already finished
    unmount();
    return { current, doneSteps };
  };

  it("marks where a normal booking is on its way", () => {
    expect(states("requested")).toEqual({ current: "1Requested", doneSteps: 0 });
    expect(states("accepted")).toEqual({ current: "2Accepted", doneSteps: 1 });
    expect(states("payment_pending")).toEqual({ current: "2Accepted", doneSteps: 1 });
    expect(states("confirmed")).toEqual({ current: "3Paid", doneSteps: 2 });
    expect(states("checked_in")).toEqual({ current: "4Staying", doneSteps: 3 });
    expect(states("completed")).toEqual({ current: "5Completed", doneSteps: 4 });
  });

  it("shows nothing for bookings that ended early", () => {
    for (const status of ["rejected", "expired", "withdrawn", "payment_expired", "cancelled"] as const) {
      const { container, unmount } = render(<TripProgress status={status} />);
      expect(container.innerHTML).toBe("");
      unmount();
    }
  });
});
