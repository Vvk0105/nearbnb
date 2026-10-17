import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { BookingItem } from "../lib/types";
import { formatCountdown } from "../components/bookings";
import { TripDetailPage, TripsPage } from "./account/Trips";
import { RequestsPage } from "./host/RequestsPage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const base: BookingItem = {
  id: "b1", reference: "NB-ABCD2345", status: "requested", hours: 48, guests: 2, currency: "AUD", timezone: "Australia/Sydney",
  check_in_local: "2099-06-15T14:00", check_out_local: "2099-06-17T14:00", check_in_label: "15 Jun 2:00 PM", check_out_label: "17 Jun 2:00 PM",
  requested_at: "2099-01-01T00:00:00Z", expires_at: "2099-01-01T00:30:00Z", payment_deadline_at: null, seconds_to_respond: 1500, seconds_to_pay: null,
  closed_reason: null, can_withdraw: true,
  property: { id: "p1", name: "Beach House", suburb: "Bondi", state: "NSW", cover_url: null }, unit: { id: "u1", name: "Entire house" }, room: null,
  price: { nights: 2, hours: 48, accommodation: 50000, cleaning_fee: 0, service_fee: 3750, service_fee_percent: 7.5, tax: 5375, tax_name: "GST", tax_rate_percent: 10, tax_inclusive: false, total: 59125 },
  host: { first_name: "Hannah" },
};
const hostSide: BookingItem = {
  ...base, can_withdraw: false, room: "1",
  price: { nights: 2, hours: 48, accommodation: 50000, cleaning_fee: 0, commission: 6000, commission_percent: 12, host_payable: 44000 },
  guest: { full_name: "Gina Guest", age: 34, member_since: "2026-01-02T00:00:00Z", email_verified: true, social_links: [{ platform: "Instagram", url: "https://instagram.com/gina" }] },
};
const counts = { pending: 1, awaiting_payment: 0, upcoming: 0, past: 0 };

function renderAt(path: string, ui: React.ReactNode, route: string) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("countdown text", () => {
  it("formats minutes and hours", () => {
    expect(formatCountdown(65)).toBe("1:05");
    expect(formatCountdown(1500)).toBe("25:00");
    expect(formatCountdown(3 * 3600 + 5 * 60)).toBe("3h 05m");
  });
});

describe("guest trips", () => {
  let calls: { url: string; method?: string }[] = [];
  let detail: BookingItem = base;
  beforeEach(() => {
    calls = [];
    detail = base;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method });
      if (url.includes("/me/bookings/b1/withdraw")) { detail = { ...base, status: "withdrawn", can_withdraw: false, seconds_to_respond: null }; return json(200, detail); }
      if (url.includes("/me/bookings/b1")) return json(200, detail);
      if (url.includes("/me/bookings/missing")) return json(404, { error: { code: "NOT_FOUND", message: "Booking not found." } });
      if (url.includes("/me/bookings")) return json(200, { items: [base, { ...base, id: "b2", status: "rejected", seconds_to_respond: null }], total: 2, page: 1, page_size: 25, counts });
      return json(404, {});
    });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("lists trips with their status and the time the host has left", async () => {
    renderAt("/account/trips", <TripsPage />, "/account/trips");
    expect(await screen.findAllByText("Beach House")).toHaveLength(2);
    expect(screen.getByText("Waiting for host")).toBeTruthy();
    expect(screen.getByText("Declined")).toBeTruthy();
    expect(screen.getByText(/Host replies within/)).toBeTruthy();
    expect(screen.getAllByText(/\$591\.25/).length).toBe(2);
  });

  it("filters by group using the server", async () => {
    renderAt("/account/trips", <TripsPage />, "/account/trips");
    await screen.findAllByText("Beach House");
    await userEvent.setup().click(screen.getByRole("tab", { name: /Past/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("group=past"))).toBe(true));
  });

  it("shows a waiting trip, never the host's contact details, and lets the guest withdraw after confirming", async () => {
    renderAt("/account/trips/b1", <TripDetailPage />, "/account/trips/:bookingId");
    expect(await screen.findByText(/Your request is with Hannah/)).toBeTruthy();
    expect(screen.getByText(/You have not been charged/)).toBeTruthy();
    expect(screen.queryByText(/Check-in instructions/)).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Withdraw this request" }));
    await user.click(screen.getByRole("button", { name: "Yes, withdraw" }));
    expect(await screen.findByText("You withdrew this request.")).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/withdraw") && c.method === "POST")).toBe(true);
  });

  it("explains a missing booking", async () => {
    renderAt("/account/trips/missing", <TripDetailPage />, "/account/trips/:bookingId");
    expect(await screen.findByText("Booking not found")).toBeTruthy();
  });

  it("shows host details and check-in instructions once confirmed", async () => {
    detail = { ...base, status: "confirmed", can_withdraw: false, seconds_to_respond: null, host: { first_name: "Hannah", name: "Hannah Host", email: "h@example.com", phone: "+61 400 111 222" }, checkin_instructions: "Key is in the lockbox: 4321" };
    renderAt("/account/trips/b1", <TripDetailPage />, "/account/trips/:bookingId");
    expect(await screen.findByText("Key is in the lockbox: 4321")).toBeTruthy();
    expect(screen.getByText(/h@example.com/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Withdraw this request" })).toBeNull();
  });
});

describe("host requests", () => {
  let calls: { url: string; method?: string; body?: { note?: string | null } }[] = [];
  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (url.includes("/accept") || url.includes("/reject")) return json(200, { ...hostSide, status: url.includes("/accept") ? "accepted" : "rejected" });
      if (url.includes("/host/bookings")) return json(200, { items: [hostSide], total: 1, page: 1, page_size: 25, counts });
      return json(404, {});
    });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("shows what the host needs to decide: dates, payout, guest summary, and hides contact details", async () => {
    renderAt("/host/requests", <RequestsPage />, "/host/requests");
    expect(await screen.findByText("Gina Guest, 34")).toBeTruthy();
    expect(screen.getByText("Needs your reply")).toBeTruthy();
    expect(screen.getByText("$440.00")).toBeTruthy();                          // what the host receives
    expect(screen.getByText(/after the 12% platform fee/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Instagram profile" }).getAttribute("href")).toBe("https://instagram.com/gina");
    expect(screen.getByText(/Contact details are shared once the guest has paid/)).toBeTruthy();
    expect(screen.getByText(/Reply within/)).toBeTruthy();
    expect(screen.queryByText(/6000|\$60\.00 service/)).toBeNull();
  });

  it("accepts with one click", async () => {
    renderAt("/host/requests", <RequestsPage />, "/host/requests");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/b1/accept") && c.method === "POST")).toBe(true));
  });

  it("declines with an optional reason the guest will see", async () => {
    renderAt("/host/requests", <RequestsPage />, "/host/requests");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Decline" }));
    await user.type(screen.getByLabelText(/Reason/), "We are renovating");
    await user.click(screen.getByRole("button", { name: "Decline request" }));
    await waitFor(() => expect(calls.find((c) => c.url.endsWith("/b1/reject"))?.body).toEqual({ note: "We are renovating" }));
  });
});
