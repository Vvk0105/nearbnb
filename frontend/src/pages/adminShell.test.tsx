import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { pageTitle } from "../components/AdminShell";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = (is_admin: boolean) => ({
  id: "1", email: "admin@example.com", full_name: "Ada Admin", is_admin, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
});

const trend = Array.from({ length: 30 }, (_, i) => ({ date: `2099-01-${String(i + 1).padStart(2, "0")}`, bookings: i % 5 === 0 ? 2 : 0, paid: i === 29 ? 29563 : 0 }));
const dashboard = {
  alerts: [
    { key: "applications", label: "Host applications to review", count: 2, href: "/admin/hosts", severity: "action" },
    { key: "failed_payments", label: "Failed payments (7 days)", count: 1, href: "/admin/payments", severity: "problem" },
    { key: "held_payouts", label: "Payouts on hold", count: 4, href: "/admin/payouts", severity: "info" },
    { key: "open_issues", label: "Reported problems to look at", count: 0, href: "/admin/issues", severity: "none" },
  ],
  trend,
  bookings: { requests_waiting: 3, awaiting_payment: 1, upcoming: 5, in_progress: 2, completed_30_days: 7, cancelled_30_days: 1 },
  money_30_days: { gross_paid: 295630, refunded: 27500, net_collected: 268130, service_fees: 18750, commission: 30000 },
  totals: { users: 40, hosts: 6, properties: 9 },
  recent: [],
};

function open(isAdmin: boolean, path = "/admin") {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/v1/me")) return json(200, me(isAdmin));
    if (url.endsWith("/v1/admin/dashboard")) return json(200, dashboard);
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("admin shell", () => {
  it("names the page from the address", () => {
    expect(pageTitle("/admin")).toBe("Dashboard");
    expect(pageTitle("/admin/payments")).toBe("Payments");
    expect(pageTitle("/admin/bookings/abc")).toBe("Bookings");
    expect(pageTitle("/admin/catalogues")).toBe("Types & amenities");
  });

  it("shows a sidebar with every area, live counts that skip info-only alerts, and the signed-in admin", async () => {
    open(true);
    const nav = await screen.findByRole("navigation", { name: "Admin" });
    for (const name of ["Dashboard", "Hosts", "Users", "Listings", "Bookings", "Payments", "Payouts", "Refunds", "Issues", "Calendar", "Settings", "Audit log", "Webhooks", "Exports"]) {
      expect(within(nav).getByRole("link", { name: new RegExp(`^${name}`) })).toBeTruthy();
    }
    expect(await within(within(nav).getByRole("link", { name: /^Hosts/ })).findByLabelText("2 need attention")).toBeTruthy();
    expect(within(within(nav).getByRole("link", { name: /^Payments/ })).getByLabelText("1 need attention")).toBeTruthy();
    expect(within(within(nav).getByRole("link", { name: /^Payouts/ })).queryByLabelText(/need attention/)).toBeNull();   // "on hold" is information, not a problem
    expect(within(within(nav).getByRole("link", { name: /^Issues/ })).queryByLabelText(/need attention/)).toBeNull();
    expect(screen.getByText("Ada Admin")).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1, name: "Dashboard" }).length).toBeGreaterThan(0);   // one for desktop, one for phones
  });

  it("draws the charts and the key numbers on the dashboard", async () => {
    open(true);
    expect(await screen.findByRole("img", { name: "Money paid by guests per day" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Booking requests per day" })).toBeTruthy();
    const keys = screen.getByRole("region", { name: "Key numbers" });
    expect(within(keys).getByText("Net collected (30 days)").nextSibling?.textContent).toBe("$2,681.30");
    expect(within(keys).getByText("Booking requests (30 days)").nextSibling?.textContent).toBe("12");   // 6 busy days x 2
    expect(within(keys).getByText("Live properties").nextSibling?.textContent).toBe("9");
  });

  it("shows problems before actions in the attention list", async () => {
    open(true);
    const alerts = await screen.findByRole("region", { name: "Alerts" });
    const labels = within(alerts).getAllByRole("link").map((l) => l.textContent ?? "");
    expect(labels[0]).toContain("Failed payments");
    expect(labels[1]).toContain("Host applications to review");
  });

  it("opens the menu on a phone and moves between pages", async () => {
    const u = userEvent.setup();
    open(true);
    await screen.findByRole("navigation", { name: "Admin" });
    await u.click(screen.getByRole("button", { name: "Open menu" }));
    const dialog = screen.getByRole("dialog", { name: "Admin menu" });
    await u.click(within(dialog).getByRole("link", { name: /^Payments/ }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Admin menu" })).toBeNull());
    expect(screen.getAllByRole("heading", { level: 1 }).some((h) => h.textContent === "Payments")).toBe(true);
  });

  it("keeps non-admins out", async () => {
    open(false);
    await waitFor(() => expect(screen.queryByRole("navigation", { name: "Admin" })).toBeNull());
    expect(await screen.findByRole("heading", { name: /Find a place for exactly the time you need/ })).toBeTruthy();
  });
});
