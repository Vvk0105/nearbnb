import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { AdminBookingDetail, AdminDashboardData, AdminUserDetail, BookingItem, HostDashboardData, HostMoney } from "../lib/types";
import { HostDashboard } from "../components/HostDashboard";
import { TripsPage } from "./account/Trips";
import { AdminBookingDetailPage } from "./admin/AdminBookingDetailPage";
import { AdminBookingsPage } from "./admin/AdminBookingsPage";
import { AdminDashboardPage } from "./admin/AdminDashboardPage";
import { AdminExportsPage, AdminPaymentsPage, AdminPayoutsPage, AdminWebhooksPage } from "./admin/AdminMoneyPages";
import { AdminUserDetailPage, AdminUsersPage } from "./admin/AdminUsersPages";
import { MoneyPage } from "./host/MoneyPage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function renderAt(path: string, route: string, ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

type Call = { url: string; method?: string; body?: Record<string, unknown> };
let calls: Call[] = [];
const record = (url: string, init?: RequestInit) => calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
const posted = (suffix: string) => calls.filter((c) => c.url.endsWith(suffix) && c.method === "POST").at(-1)?.body;      // the most recent one
const stub = (handler: (url: string, init?: RequestInit) => Promise<Response>) => vi.stubGlobal("fetch", (url: string, init?: RequestInit) => { record(url, init); return handler(url, init); });
beforeEach(() => { calls = []; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const dashboard: AdminDashboardData = {
  alerts: [
    { key: "applications", label: "Host applications to review", count: 2, href: "/admin/hosts", severity: "action" },
    { key: "failed_refunds", label: "Refunds that failed", count: 1, href: "/admin/refunds", severity: "problem" },
    { key: "held_payouts", label: "Payouts on hold", count: 0, href: "/admin/payouts", severity: "none" },
  ],
  bookings: { requests_waiting: 3, awaiting_payment: 1, upcoming: 5, in_progress: 2, completed_30_days: 7, cancelled_30_days: 1 },
  money_30_days: { gross_paid: 295630, refunded: 27500, net_collected: 268130, service_fees: 18750, commission: 30000 },
  totals: { users: 40, hosts: 6, properties: 9 },
  recent: [{ id: 1, action: "booking.cancelled_by_host", entity_type: "booking", entity_id: "x", actor: "host@example.com", at: "2099-01-01T10:00:00Z", reason: "Burst pipe" }],
};

describe("admin dashboard", () => {
  it("shows what needs attention as links, with problems standing out, plus bookings and money", async () => {
    stub(() => json(200, dashboard));
    renderAt("/admin", "/admin", <AdminDashboardPage />);
    expect(await screen.findByText((_, el) => el?.tagName === "P" && el.textContent === "2 things need your attention.")).toBeTruthy();
    const alerts = screen.getByRole("region", { name: "Alerts" });
    expect(within(alerts).getByRole("link", { name: /Host applications to review/ }).getAttribute("href")).toBe("/admin/hosts");
    const failed = within(alerts).getByRole("link", { name: /Refunds that failed/ });
    expect(failed.getAttribute("href")).toBe("/admin/refunds");
    expect(failed.className).toContain("red");
    expect(within(failed).getByText("Something is wrong")).toBeTruthy();
    expect(within(alerts).getByRole("link", { name: /Payouts on hold/ }).className).not.toContain("amber");
    expect(screen.getByText("Requests waiting").nextSibling?.textContent).toBe("3");
    expect(screen.getByText("Paid by guests").nextSibling?.textContent).toBe("$2,956.30");
    expect(screen.getByText("Net collected").nextSibling?.textContent).toBe("$2,681.30");
    expect(screen.getByText("booking.cancelled_by_host")).toBeTruthy();
  });

  it("says so when nothing needs attention", async () => {
    stub(() => json(200, { ...dashboard, alerts: dashboard.alerts.map((a) => ({ ...a, count: 0, severity: "none" })) }));
    renderAt("/admin", "/admin", <AdminDashboardPage />);
    expect(await screen.findByText("Nothing needs your attention right now.")).toBeTruthy();
  });
});

describe("admin users", () => {
  const user = { id: "u1", email: "gina@example.com", full_name: "Gina Guest", status: "active", is_admin: false, is_host: false, host_state: null, bookings: 2, created_at: "2099-01-01T00:00:00Z", last_login_at: null };
  const detail: AdminUserDetail = { ...user, phone: "+61 400 222 333", dob: "1990-05-05", email_verified: true, social_links: [{ platform: "Instagram", url: "https://instagram.com/gina" }], host_id: null, recent_bookings: [], history: [{ at: "2099-01-01T00:00:00Z", action: "booking.requested", entity_type: "booking", actor: "gina@example.com", reason: null }] };

  it("searches and filters on the server", async () => {
    stub(() => json(200, { total: 1, page: 1, page_size: 25, items: [user] }));
    renderAt("/admin/users", "/admin/users", <AdminUsersPage />);
    expect(await screen.findByRole("link", { name: "Gina Guest" })).toBeTruthy();
    expect(screen.getByText("never")).toBeTruthy();
    const u = userEvent.setup();
    await u.type(screen.getByLabelText("Search"), "gina");
    await u.selectOptions(screen.getByLabelText("Status"), "suspended");
    await u.selectOptions(screen.getByLabelText("Role"), "host");
    await waitFor(() => {
      const last = new URL(calls.at(-1)!.url, "http://x").searchParams;
      expect([last.get("q"), last.get("status"), last.get("role")]).toEqual(["gina", "suspended", "host"]);
    });
  });

  it("suspends only with a reason, then offers reactivation", async () => {
    let current = detail;
    stub((url, init) => {
      if (init?.method === "POST") { current = { ...detail, status: url.endsWith("/suspend") ? "suspended" : "active" }; return json(200, current); }
      return json(200, current);
    });
    renderAt("/admin/users/u1", "/admin/users/:userId", <AdminUserDetailPage />);
    const u = userEvent.setup();
    const suspend = (await screen.findByRole("button", { name: "Suspend user" })) as HTMLButtonElement;
    expect(suspend.disabled).toBe(true);
    expect(screen.getByText("+61 400 222 333")).toBeTruthy();
    await u.type(screen.getByLabelText(/Reason/), "Fraud report");
    await u.click(suspend);
    await waitFor(() => expect(posted("/suspend")).toEqual({ reason: "Fraud report" }));
    expect(await screen.findByText(/This account is suspended/)).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Reactivate" }));
    await waitFor(() => expect(posted("/reactivate")).toBeTruthy());
    expect(await screen.findByRole("button", { name: "Suspend user" })).toBeTruthy();
  });

  it("offers no suspend button for administrators", async () => {
    stub(() => json(200, { ...detail, is_admin: true }));
    renderAt("/admin/users/u1", "/admin/users/:userId", <AdminUserDetailPage />);
    await screen.findByText("Gina Guest");
    expect(screen.queryByRole("button", { name: "Suspend user" })).toBeNull();
  });
});

describe("admin money screens", () => {
  it("lists payments with provider references and failures, and searches", async () => {
    stub(() => json(200, { total: 1, page: 1, page_size: 25, items: [{ id: "p1", booking_id: "b1", reference: "NB-ABCD2345", guest: "gina@example.com", amount: 29563, currency: "AUD", status: "failed", attempt: 2,
      failure_code: "card_declined", failure_message: "Your card was declined.", intent_id: "pi_123", charge_id: null, created_at: "2099-01-01T00:00:00Z", succeeded_at: null }] }));
    renderAt("/admin/payments", "/admin/payments", <AdminPaymentsPage />);
    expect(await screen.findByText("$295.63")).toBeTruthy();
    expect(screen.getByText(/card_declined: Your card was declined\./)).toBeTruthy();
    expect(screen.getByText(/attempt 2/)).toBeTruthy();
    expect(screen.getByText("pi_123")).toBeTruthy();
    expect(screen.getByRole("link", { name: "NB-ABCD2345" }).getAttribute("href")).toBe("/admin/bookings/b1");
    await userEvent.setup().type(screen.getByLabelText("Search"), "pi_1");
    await waitFor(() => expect(new URL(calls.at(-1)!.url, "http://x").searchParams.get("q")).toBe("pi_1"));
  });

  it("holds, releases and retries payouts", async () => {
    const row = (state: string, extra = {}) => ({ booking_id: "b1", reference: "NB-ABCD2345", host: "Hannah Host", host_email: "h@example.com", currency: "AUD", check_in: "2099-01-01T00:00:00Z", stripe_transfer_id: null, state, amount: 22000, eligible_at: "2099-01-02T00:00:00Z", ...extra });
    let items = [row("scheduled")];
    stub((url, init) => (init?.method === "POST" ? json(200, {}) : json(200, { total: items.length, page: 1, page_size: 25, items })));
    renderAt("/admin/payouts", "/admin/payouts", <AdminPayoutsPage />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Hold" }));
    await waitFor(() => expect(posted("/b1/payout-hold")).toEqual({ hold: true, note: "Held by admin" }));
    items = [row("failed", { error: "Stripe refused" })];
    await u.selectOptions(screen.getByLabelText("Show"), "failed");
    expect(await screen.findByText("Stripe refused")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(posted("/b1/payout-retry")).toBeUndefined());                   // retry carries no body but is a POST
    expect(calls.some((c) => c.url.endsWith("/b1/payout-retry") && c.method === "POST")).toBe(true);
    items = [row("held", { hold_note: "dispute" })];
    await u.selectOptions(screen.getByLabelText("Show"), "held");
    await u.click(await screen.findByRole("button", { name: "Release hold" }));
    await waitFor(() => expect(posted("/b1/payout-hold")).toEqual({ hold: false }));
  });

  it("runs a failed webhook again and reports whether it worked", async () => {
    let result = { processed: false, error: "still broken" };
    stub((url, init) => (init?.method === "POST" ? json(200, result) : json(200, { total: 1, page: 1, page_size: 25, items: [{ id: "e1", event_id: "evt_1", type: "payment_intent.succeeded", created_at: "2099-01-01T00:00:00Z", processed_at: null, error: "database was down" }] })));
    renderAt("/admin/webhooks", "/admin/webhooks", <AdminWebhooksPage />);
    const u = userEvent.setup();
    expect(await screen.findByText("database was down")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Run again" }));
    expect(await screen.findByText(/Still failing: still broken/)).toBeTruthy();
    result = { processed: true, error: "" } as never;
    await u.click(screen.getByRole("button", { name: "Run again" }));
    expect(await screen.findByText("Processed successfully.")).toBeTruthy();
    await u.click(screen.getByLabelText(/Only show failed events/));
    await waitFor(() => expect(calls.at(-1)!.url).toContain("failed=false"));
  });

  it("offers each CSV with the chosen dates in the link", async () => {
    renderAt("/admin/exports", "/admin/exports", <AdminExportsPage />);
    const u = userEvent.setup();
    const hrefs = () => screen.getAllByRole("link", { name: "Download CSV" }).map((a) => a.getAttribute("href") ?? "");
    expect(hrefs()).toHaveLength(4);
    expect(hrefs()[0]).toMatch(/\/admin\/exports\/bookings\.csv$/);
    await u.type(screen.getByLabelText("From"), "2026-01-01");
    await u.type(screen.getByLabelText(/To/), "2026-01-31");
    expect(hrefs().map((h) => h.split("/admin/exports/")[1])).toEqual([
      "bookings.csv?from=2026-01-01&to=2026-01-31", "payments.csv?from=2026-01-01&to=2026-01-31", "refunds.csv?from=2026-01-01&to=2026-01-31", "payouts.csv?from=2026-01-01&to=2026-01-31",
    ]);
    expect(screen.getAllByRole("link", { name: "Download CSV" })[0].hasAttribute("download")).toBe(true);
  });
});

describe("admin bookings", () => {
  const detail = {
    id: "b1", reference: "NB-ABCD2345", status: "cancelled", hours: 24, guests: 2, currency: "AUD", timezone: "Australia/Sydney", check_in_label: "15 Jun 2:00 PM", check_out_label: "16 Jun 2:00 PM",
    requested_at: "2099-01-01T00:00:00Z", property: { id: "p1", name: "Beach House", suburb: "Bondi", state: "NSW", cover_url: null }, unit: { id: "u1", name: "Entire house" }, room: "1",
    price: { accommodation: 25000, cleaning_fee: 0, service_fee: 1875, tax: 2688, total: 29563, commission: 3000, commission_percent: 12, host_payable: 22000 },
    guest: { full_name: "Gina Guest", email: "gina@example.com", phone: "+61 400 222 333" }, host: { id: "h1", name: "Hannah", email: "h@example.com" },
    cancelled: { by: "guest", at: "x", reason: null, policy: "Moderate", refund_pct: 50 }, payout: { state: "scheduled", amount: 11000, eligible_at: "2099-06-16T14:00:00Z" },
    timeline: [{ at: "2099-01-01T01:00:00Z", from: "requested", to: "accepted", actor: "h@example.com", note: null }, { at: "2099-01-02T00:00:00Z", from: "payment_pending", to: "confirmed", actor: null, note: "payment succeeded" }],
    payments: [{ id: "pay1", status: "succeeded", amount: 29563, attempt: 1, intent_id: "pi_1", charge_id: "ch_1", failure_message: null, created_at: "2099-01-02T00:00:00Z" }],
    refunds: [{ id: "r1", type: "policy", status: "succeeded", amount: 13750, reason: null, error: null, stripe_refund_id: "re_1", host_reversal_amount: 0, created_at: "2099-01-03T00:00:00Z" }],
    refund_requests: [], invoice: { number: "INV-000001", issued_at: "2099-01-02T00:00:00Z" }, settlement: null,
    audit: [{ at: "2099-01-03T00:00:00Z", action: "booking.cancelled_by_guest", actor: "gina@example.com", reason: null }],
  } as unknown as AdminBookingDetail;

  it("shows the whole story of one booking", async () => {
    stub(() => json(200, detail));
    renderAt("/admin/bookings/b1", "/admin/bookings/:bookingId", <AdminBookingDetailPage />);
    expect(await screen.findByText("NB-ABCD2345")).toBeTruthy();
    expect(screen.getByText("Invoice INV-000001")).toBeTruthy();
    expect(screen.getByText("Guest paid").nextSibling?.textContent).toBe("$295.63");
    expect(screen.getByText(/Platform commission \(12%\)/).nextSibling?.textContent).toBe("$30.00");
    expect(screen.getByText(/payment succeeded/)).toBeTruthy();
    expect(screen.getByText(/\(automatic\)/)).toBeTruthy();
    expect(screen.getByText("pi_1")).toBeTruthy();
    expect(screen.getByText(/policy · \$137\.50/)).toBeTruthy();
    expect(screen.getByText("booking.cancelled_by_guest")).toBeTruthy();
    expect(screen.getByText(/Cancelled by guest/)).toBeTruthy();
  });

  it("searches the bookings list and links each row to its detail page", async () => {
    stub(() => json(200, { items: [{ ...detail, status: "confirmed", cancelled: undefined, payout: undefined }], total: 1, page: 1, page_size: 25, counts: { pending: 0, awaiting_payment: 0, upcoming: 1, past: 0 } }));
    renderAt("/admin/bookings", "/admin/bookings", <AdminBookingsPage />);
    expect((await screen.findByRole("link", { name: "NB-ABCD2345" })).getAttribute("href")).toBe("/admin/bookings/b1");
    await userEvent.setup().type(screen.getByLabelText("Search"), "gina");
    await waitFor(() => expect(new URL(calls.at(-1)!.url, "http://x").searchParams.get("q")).toBe("gina"));
  });
});

describe("host dashboard and money page", () => {
  const stay = (id: string, over = {}): BookingItem => ({
    id, reference: "NB-" + id, status: "confirmed", hours: 24, guests: 2, currency: "AUD", timezone: "Australia/Sydney", check_in_local: "", check_out_local: "", check_in_label: "15 Jun 2:00 PM", check_out_label: "16 Jun 2:00 PM",
    requested_at: null, expires_at: null, payment_deadline_at: null, seconds_to_respond: null, seconds_to_pay: null, closed_reason: null, can_withdraw: false,
    property: { id: "p", name: "Beach House", suburb: "", state: "", cover_url: null }, unit: { id: "u", name: "Entire house" }, room: "1", price: { host_payable: 22000 },
    guest: { full_name: "Gina Guest", age: 34, member_since: null, email_verified: true, social_links: [] }, payout: { state: "scheduled", amount: 22000, eligible_at: null }, ...over,
  }) as BookingItem;
  const hostData: HostDashboardData = {
    state: "active", requests: { pending: 2, awaiting_payment: 1 }, current_stays: [stay("now")], upcoming_stays: [stay("later")], listings: { approved: 2, submitted: 1 },
    money: { paid_out: 100000, scheduled: 44000, this_month: 66000, deductions: 0, next_payout: { at: "2099-06-16T14:00:00Z", amount: 22000, reference: "NB-later" } }, needs_attention: ["2 booking request(s) waiting for your reply."],
  };

  it("shows requests, who is staying, who is coming, listings and the next payout", async () => {
    stub(() => json(200, hostData));
    renderAt("/host", "/host", <HostDashboard />);
    expect(await screen.findByText(/2 booking request\(s\) waiting/)).toBeTruthy();
    const overview = screen.getByRole("region", { name: "Overview" });
    expect(within(overview).getByText("Requests waiting for you").nextSibling?.textContent).toBe("2");
    expect(within(overview).getByText("Next payout").nextSibling?.textContent).toBe("$220.00");
    expect(screen.getByText(/after 2099-06-16/)).toBeTruthy();
    expect(screen.getByText("Guests staying now")).toBeTruthy();
    expect(screen.getAllByText(/Gina Guest, 2 guests/)).toHaveLength(2);
    expect(screen.getByText("2 approved · 1 submitted")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Earnings and payouts" }).getAttribute("href")).toBe("/host/money");
  });

  it("says when nobody is staying", async () => {
    stub(() => json(200, { ...hostData, current_stays: [], upcoming_stays: [], listings: {}, money: { ...hostData.money, next_payout: null }, needs_attention: [] }));
    renderAt("/host", "/host", <HostDashboard />);
    expect(await screen.findByText("Nobody is staying right now.")).toBeTruthy();
    expect(screen.getByText("No confirmed stays coming up.")).toBeTruthy();
    expect(screen.getByText("None yet")).toBeTruthy();
  });

  const money: HostMoney = {
    total: 1, page: 1, page_size: 25, summary: { paid_out: 22000, scheduled: 0, this_month: 22000, deductions: 2000, next_payout: null },
    items: [{ booking_id: "b1", reference: "NB-ABCD2345", property: "Beach House", status: "completed", currency: "AUD", check_in: "2099-06-15T04:00:00Z", check_in_label: "2099-06-15 14:00", nights: 1,
      gross: 25000, commission: 3000, host_share: 22000, deductions: 2000, net: 20000, reversed: 0, payout_state: "paid", eligible_at: null, transferred_at: "2099-06-17T00:00:00Z" }],
    bank_payouts: [{ id: "po_1", amount: 20000, currency: "AUD", status: "paid", arrival_date: "2099-06-19T00:00:00Z", failure_code: null }],
  };

  it("shows earnings per booking: value, commission, share, refunds and what the host receives, never the guest's total", async () => {
    stub(() => json(200, money));
    renderAt("/host/money", "/host/money", <MoneyPage />);
    expect(await screen.findByText("Beach House")).toBeTruthy();
    const summary = screen.getByRole("region", { name: "Summary" });
    expect(within(summary).getByText("Paid out so far").nextSibling?.textContent).toBe("$220.00");
    expect(within(summary).getByText("Next payout").nextSibling?.textContent).toBe("None scheduled");
    const row = screen.getByText("NB-ABCD2345").closest("tr") as HTMLElement;
    expect(within(row).getByText("$250.00")).toBeTruthy();
    expect(within(row).getByText("-$30.00")).toBeTruthy();
    expect(within(row).getByText("$220.00")).toBeTruthy();
    expect(within(row).getByText("-$20.00")).toBeTruthy();
    expect(within(row).getByText("$200.00")).toBeTruthy();
    expect(within(row).getByText("Paid")).toBeTruthy();
    expect(screen.queryByText(/\$295\.63/)).toBeNull();
    expect(screen.getByText(/\$20\.00 has been taken from your earnings/)).toBeTruthy();
    expect(screen.getByText(/po_1|\$200\.00 · paid/)).toBeTruthy();
  });

  it("filters by month and points the CSV link at the same month", async () => {
    stub(() => json(200, money));
    renderAt("/host/money", "/host/money", <MoneyPage />);
    await screen.findByText("Beach House");
    expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href")).toMatch(/\/host\/money\/export\.csv$/);
    await userEvent.setup().type(screen.getByLabelText("Month of check-in"), "2099-06");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("month=2099-06"));
    expect(screen.getByRole("link", { name: "Download CSV" }).getAttribute("href")).toMatch(/export\.csv\?month=2099-06$/);
  });

  it("says when there are no earnings yet", async () => {
    stub(() => json(200, { ...money, total: 0, items: [], bank_payouts: [] }));
    renderAt("/host/money", "/host/money", <MoneyPage />);
    expect(await screen.findByText("No earnings yet.")).toBeTruthy();
    expect(screen.getByText(/None yet\. Once Stripe pays/)).toBeTruthy();
  });
});

describe("guest trips", () => {
  it("has a Cancelled tab that asks the server for cancelled bookings only", async () => {
    stub(() => json(200, { items: [], total: 0, page: 1, page_size: 25, counts: { pending: 0, awaiting_payment: 0, upcoming: 0, past: 0 } }));
    renderAt("/account/trips", "/account/trips", <TripsPage />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Cancelled" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=cancelled"))).toBe(true));
  });
});
