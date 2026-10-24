import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { PRESETS } from "./admin/AdminRevenuePage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "admin@example.com", full_name: "Ada Admin", is_admin: true, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};
const revenue = (from: string, to: string, hostId: string | null = null) => ({
  from, to, currency: "AUD",
  totals: { guest_payments: 295630, refunds: 27500, net_collected: 268130, service_fees: 18750, commission: 30000, platform_revenue: 48750, tax: 26874, paid_to_hosts: 220000, bookings: 2, average_booking: 147815 },
  series: [{ date: from, paid: 295630, refunded: 0, platform_revenue: 48750, bookings: 2 }, { date: to, paid: 0, refunded: 27500, platform_revenue: 0, bookings: 0 }],
  top_properties: [{ property: "Beach House", host: "Hannah Host", bookings: 2, booking_value: 295630, platform_revenue: 48750 }],
  host_id: hostId,
  by_host: [
    { host_id: "h1", host: "Hannah Host", email: "hannah@example.com", bookings: 2, booking_value: 295630, service_fees: 18750, commission: 30000, platform_revenue: 48750, host_earnings: 247000, paid_out: 220000 },
    { host_id: "h2", host: "Hugo Host", email: "hugo@example.com", bookings: 0, booking_value: 0, service_fees: 0, commission: 0, platform_revenue: 0, host_earnings: 0, paid_out: 5000 },
  ],
  host_options: [{ host_id: "h1", host: "Hannah Host" }, { host_id: "h2", host: "Hugo Host" }],
});

let urls: string[] = [];
function open() {
  urls = [];
  vi.stubGlobal("fetch", (url: string) => {
    urls.push(url);
    if (url.endsWith("/v1/me")) return json(200, me);
    const m = /\/v1\/admin\/revenue\?from=([\d-]+)&to=([\d-]+)(?:&host_id=(\w+))?/.exec(url);
    if (m) return json(200, revenue(m[1], m[2], m[3] ?? null));
    if (url.endsWith("/v1/admin/dashboard")) return json(200, { alerts: [], bookings: {}, money_30_days: {}, totals: {}, recent: [] });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/admin/revenue"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("admin revenue page", () => {
  it("shows platform revenue and where it comes from, plus the other money lines", async () => {
    open();
    const summary = await screen.findByRole("region", { name: "Revenue summary" });
    expect(within(summary).getByText("Platform revenue").nextSibling?.textContent).toBe("$487.50");
    expect(within(summary).getByText(/\$187\.50 service fees \+ \$300\.00 commission/)).toBeTruthy();
    expect(within(summary).getByText("Guest payments").nextSibling?.textContent).toBe("$2,956.30");
    expect(within(summary).getByText("Refunds").nextSibling?.textContent).toBe("$275.00");
    expect(within(summary).getByText("Net collected").nextSibling?.textContent).toBe("$2,681.30");
    expect(within(summary).getByText("Paid to hosts").nextSibling?.textContent).toBe("$2,200.00");
    expect(within(summary).getByText("Bookings confirmed").nextSibling?.textContent).toBe("2");
    expect(screen.getByText(/Average \$1,478\.15 each/)).toBeTruthy();
  });

  it("draws both charts and lists the top properties", async () => {
    open();
    expect(await screen.findByRole("img", { name: "Platform revenue per day" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Guest payments per day" })).toBeTruthy();
    const row = screen.getByText("Beach House").closest("tr") as HTMLElement;
    expect(within(row).getByText("Hannah Host")).toBeTruthy();
    expect(within(row).getByText("$487.50")).toBeTruthy();
  });

  it("starts on the last 30 days and asks the server for exactly the chosen dates", async () => {
    const u = userEvent.setup();
    open();
    await screen.findByRole("region", { name: "Revenue summary" });
    const first = urls.find((x) => x.includes("/admin/revenue?"))!;
    const [from, to] = PRESETS[1].range();
    expect(first).toContain(`from=${from}&to=${to}`);
    expect(screen.getByRole("button", { name: "Last 30 days" }).getAttribute("aria-pressed")).toBe("true");

    await u.click(screen.getByRole("button", { name: "Last 7 days" }));
    const [from7, to7] = PRESETS[0].range();
    await waitFor(() => expect(urls.some((x) => x.includes(`from=${from7}&to=${to7}`))).toBe(true));
    expect(screen.getByRole("button", { name: "Last 7 days" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("lets the admin type their own dates, and warns about a backwards range without asking the server", async () => {
    const u = userEvent.setup();
    open();
    await screen.findByRole("region", { name: "Revenue summary" });
    const fromBox = screen.getByLabelText("From") as HTMLInputElement;
    await u.clear(fromBox);
    await u.type(fromBox, "2026-03-01");
    await waitFor(() => expect(urls.some((x) => x.includes("from=2026-03-01"))).toBe(true));
    expect(screen.getByRole("button", { name: "Last 30 days" }).getAttribute("aria-pressed")).toBe("false");
    const before = urls.length;
    const toBox = screen.getByLabelText("To") as HTMLInputElement;
    await u.clear(toBox);
    await u.type(toBox, "2026-02-01");
    expect(await screen.findByText(/start date that is on or before the end date/)).toBeTruthy();
    expect(urls.slice(before).some((x) => x.includes("to=2026-02-01"))).toBe(false);
  });

  it("offers the CSV for the same dates", async () => {
    open();
    await screen.findByRole("region", { name: "Revenue summary" });
    const [from, to] = PRESETS[1].range();
    const link = screen.getByRole("link", { name: /Download CSV/ });
    expect(link.getAttribute("href")).toContain(`/admin/revenue.csv?from=${from}&to=${to}`);
  });

  it("is in the admin menu under bookings and money", async () => {
    open();
    const nav = await screen.findByRole("navigation", { name: "Admin" });
    expect(within(nav).getByRole("link", { name: /^Revenue/ }).getAttribute("href")).toBe("/admin/revenue");
  });

  it("presets cover this month and last month correctly", () => {
    const [mFrom, mTo] = PRESETS.find((p) => p.key === "month")!.range();
    expect(mFrom.endsWith("-01") && mFrom.slice(0, 7) === mTo.slice(0, 7)).toBe(true);
    const [lFrom, lTo] = PRESETS.find((p) => p.key === "lastmonth")!.range();
    expect(lFrom.endsWith("-01")).toBe(true);
    expect(new Date(lTo).getTime() - new Date(lFrom).getTime()).toBeGreaterThanOrEqual(27 * 86400000);
    expect(lTo < mFrom).toBe(true);
  });

  it("lists every host with what we earned from them, what they earned and what was sent to them", async () => {
    open();
    const row = (await screen.findByRole("link", { name: "Hannah Host" })).closest("tr") as HTMLElement;
    expect(within(row).getByText("hannah@example.com")).toBeTruthy();
    expect(within(row).getByText("$487.50")).toBeTruthy();       // platform revenue
    expect(within(row).getByText("$2,470.00")).toBeTruthy();     // what the host earned
    expect(within(row).getByText("$2,200.00")).toBeTruthy();     // what was sent to them
    expect(screen.getByRole("link", { name: "Hannah Host" }).getAttribute("href")).toBe("/admin/hosts/h1");
    const quiet = screen.getByRole("link", { name: "Hugo Host" }).closest("tr") as HTMLElement;
    expect(within(quiet).getByText("$50.00")).toBeTruthy();       // a host paid out in the period without new bookings still shows
  });

  it("limits every number to one host from the picker, and goes back to everyone", async () => {
    const u = userEvent.setup();
    open();
    await screen.findByRole("region", { name: "Revenue summary" });
    await screen.findByRole("option", { name: "Hannah Host" });
    await u.selectOptions(screen.getByLabelText("Host"), "h1");
    await waitFor(() => expect(urls.some((x) => x.includes("&host_id=h1"))).toBe(true));
    expect(await screen.findByText(/Showing numbers for/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Download CSV/ }).getAttribute("href")).toContain("&host_id=h1");
    await u.click(screen.getByRole("button", { name: "Show all hosts" }));
    expect(screen.queryByText(/Showing numbers for/)).toBeNull();
    expect((screen.getByLabelText("Host") as HTMLSelectElement).value).toBe("");
  });

  it("offers a per-host CSV for the same dates", async () => {
    open();
    await screen.findByRole("region", { name: "Revenue summary" });
    const [from, to] = PRESETS[1].range();
    expect(screen.getByRole("link", { name: /Download by host/ }).getAttribute("href")).toContain(`/admin/revenue.csv?from=${from}&to=${to}&by=host`);
  });
});
