import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { hostPageTitle } from "../components/HostShell";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "host@example.com", full_name: "Hannah Host", is_admin: false, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};
const hostInfo = (state: string) => ({
  state, id: "h1", review_status: "approved", review_note: null, editable: false, application: {}, documents: [],
  steps: [{ key: "apply", label: "Apply", done: true }, { key: "approve", label: "Approved", done: true }, { key: "stripe", label: "Payouts", done: state === "active" }, { key: "list", label: "First listing", done: false }],
  stripe: { connected: true, details_submitted: true, payouts_enabled: true, transfers_active: true, ready: true, requirements: [], dev_mode: false },
  can_create_listings: true, can_submit_listings: true,
});
const dashboard = {
  state: "active", requests: { pending: 2, awaiting_payment: 1 }, current_stays: [], upcoming_stays: [], listings: { approved: 1 },
  money: { paid_out: 100000, scheduled: 44000, this_month: 66000, deductions: 0, next_payout: null }, needs_attention: [],
};

function open(state: string, path = "/host") {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/v1/me")) return json(200, me);
    if (url.endsWith("/v1/host")) return json(200, hostInfo(state));
    if (url.endsWith("/v1/host/dashboard")) return json(200, dashboard);
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("host area shell", () => {
  it("names pages from the address", () => {
    expect(hostPageTitle("/host")).toBe("Dashboard");
    expect(hostPageTitle("/host/requests")).toBe("Booking requests");
    expect(hostPageTitle("/host/properties/p1/units/u1")).toBe("Properties");
    expect(hostPageTitle("/host/money")).toBe("Earnings");
  });

  it("gives an active host a sidebar with a live request count and a way back to their trips", async () => {
    open("active");
    const nav = await screen.findByRole("navigation", { name: "Host" });
    for (const name of ["Dashboard", "Booking requests", "Properties", "Earnings", "Payout setup", "Application"]) {
      expect(within(nav).getByRole("link", { name })).toBeTruthy();
    }
    expect(await within(within(nav).getByRole("link", { name: /^Booking requests/ })).findByLabelText("2 need attention")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: /My trips/ })[0].getAttribute("href")).toBe("/account/trips");
  });

  it("shows the key numbers, and only one page heading (the shell title is not a second h1)", async () => {
    open("active");
    const overview = await screen.findByRole("region", { name: "Overview" });
    expect(within(overview).getByText("Requests waiting for you").nextSibling?.textContent).toBe("2");
    expect(within(overview).getByText("This month's stays").nextSibling?.textContent).toBe("$660.00");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("shows set-up progress to a host who is not active yet, without asking for dashboard data", async () => {
    open("approved");
    const progress = await screen.findByRole("region", { name: "Setup progress" });
    expect(within(progress).getByText("2 of 4 done")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Overview" })).toBeNull();
  });

  it("opens the menu on a phone", async () => {
    const u = userEvent.setup();
    open("active");
    await screen.findByRole("navigation", { name: "Host" });
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    const dialog = screen.getByRole("dialog", { name: "Host menu" });
    await u.click(within(dialog).getByRole("link", { name: /^Earnings/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Host menu" })).toBeNull());
  });
});
