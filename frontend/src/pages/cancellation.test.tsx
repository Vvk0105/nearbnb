import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { BookingItem, CancelPreview, CancellationTemplate, RefundRequestItem, RefundRow } from "../lib/types";
import { describeTiers } from "../components/CancelBooking";
import { TripDetailPage } from "./account/Trips";
import { RequestsPage } from "./host/RequestsPage";
import { AdminBookingsPage } from "./admin/AdminBookingsPage";
import { AdminCancellationPage } from "./admin/AdminCancellationPage";
import { AdminRefundsPage } from "./admin/AdminRefundsPage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const base: BookingItem = {
  id: "b1", reference: "NB-ABCD2345", status: "confirmed", hours: 24, guests: 2, currency: "AUD", timezone: "Australia/Sydney",
  check_in_local: "2099-06-15T14:00", check_out_local: "2099-06-16T14:00", check_in_label: "15 Jun 2:00 PM", check_out_label: "16 Jun 2:00 PM",
  requested_at: "2099-01-01T00:00:00Z", expires_at: null, payment_deadline_at: null, seconds_to_respond: null, seconds_to_pay: null,
  closed_reason: null, can_withdraw: false, can_pay: false, invoice_number: "INV-000001",
  property: { id: "p1", name: "Beach House", suburb: "Bondi", state: "NSW", cover_url: null }, unit: { id: "u1", name: "Entire house" }, room: null,
  price: { nights: 1, hours: 24, accommodation: 25000, cleaning_fee: 0, service_fee: 1875, service_fee_percent: 7.5, tax: 2688, tax_name: "GST", tax_rate_percent: 10, tax_inclusive: false, total: 29563 },
  host: { first_name: "Hannah", name: "Hannah Host", email: "h@example.com", phone: "+61 400 111 222" },
};

const preview = (total: number, pct: number, hours = 240): CancelPreview => ({
  booking_id: "b1", currency: "AUD", paid: 29563, service_fee_refundable: false,
  policy: { name: "Moderate", tiers: [{ hours_before: 120, refund_pct: 100 }, { hours_before: 24, refund_pct: 50 }] },
  refund: { mode: "policy", policy_name: "Moderate", hours_before: hours, refund_pct: pct, accommodation: Math.round(25000 * pct / 100), cleaning_fee: 0, service_fee: 0, tax: total - Math.round(25000 * pct / 100), total,
    host_accommodation: 0, host_cleaning_fee: 0, host_commission: 0, host_payable: 0 },
});

function renderAt(path: string, route: string, ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

let calls: { url: string; method?: string; body?: Record<string, unknown> }[] = [];
const bodyOf = (suffix: string) => calls.find((c) => c.url.endsWith(suffix) && c.method && c.method !== "GET")?.body;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("describing a policy in words", () => {
  it("lists each step and the cut-off", () => {
    expect(describeTiers([{ hours_before: 120, refund_pct: 100 }, { hours_before: 24, refund_pct: 50 }])).toEqual([
      "100% refund of the accommodation if you cancel at least 5 days before check-in",
      "50% refund of the accommodation if you cancel at least 1 day before check-in",
      "No refund after that",
    ]);
    expect(describeTiers([{ hours_before: 36, refund_pct: 100 }])[0]).toContain("36 hours");
    expect(describeTiers([])).toEqual(["No refund once the booking is confirmed."]);
  });
});

describe("guest cancels a confirmed booking", () => {
  let current: BookingItem;
  let nextPreview: CancelPreview;
  let cancelResponse: () => Promise<Response>;
  beforeEach(() => {
    calls = [];
    current = base;
    nextPreview = preview(27500, 100);
    cancelResponse = () => { current = { ...base, status: "cancelled", cancelled: { by: "guest", at: "2099-01-02T00:00:00Z", reason: null, policy: "Moderate", refund_pct: 100 }, refund: { refunded: 27500, pending: 0, failed: 0, state: "refunded" } }; return json(200, current); };
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (url.endsWith("/cancel-preview")) return json(200, nextPreview);
      if (url.endsWith("/cancel")) return cancelResponse();
      if (url.includes("/me/bookings/b1")) return json(200, current);
      return json(404, {});
    });
  });

  it("shows the policy and exactly what would be refunded before asking to confirm", async () => {
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Cancel this booking" }));
    expect(await screen.findByText("Moderate cancellation policy")).toBeTruthy();
    expect(screen.getByText(/100% refund of the accommodation if you cancel at least 5 days/)).toBeTruthy();
    const dl = screen.getByLabelText("Refund breakdown");
    expect(within(dl).getByText("You paid").nextSibling?.textContent).toBe("$295.63");
    expect(within(dl).getByText(/Service fee \(not refundable\)/)).toBeTruthy();
    expect(within(dl).getByText("Refund to your card").nextSibling?.textContent).toBe("$275.00");
    expect(screen.getByRole("button", { name: "Cancel and refund $275.00" })).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/cancel") && c.method === "POST")).toBe(false);          // nothing happens until they confirm
  });

  it("sends the amount the guest saw, then shows the cancelled booking and the refund", async () => {
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel this booking" }));
    await user.click(await screen.findByRole("button", { name: "Cancel and refund $275.00" }));
    await waitFor(() => expect(bodyOf("/cancel")).toEqual({ expected_refund: 27500 }));
    expect(await screen.findByText(/You cancelled this booking/)).toBeTruthy();
    expect(screen.getByText(/\$275\.00 has been refunded to your card/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel this booking" })).toBeNull();
  });

  it("asks again with the new amount when time moved them into a smaller refund", async () => {
    cancelResponse = () => { nextPreview = preview(13750, 50, 60); return json(409, { error: { code: "REFUND_CHANGED", message: "The refund changed to $137.50 while you were deciding. Please check it and confirm again." } }); };
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel this booking" }));
    await user.click(await screen.findByRole("button", { name: "Cancel and refund $275.00" }));
    expect(await screen.findByText(/The refund changed to \$137\.50/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Cancel and refund $137.50" })).toBeTruthy();   // the guest must confirm the new amount
    expect(screen.getAllByText(/50%/).length).toBeGreaterThan(0);
  });

  it("is honest when nothing would be refunded", async () => {
    nextPreview = preview(0, 0, 10);
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Cancel this booking" }));
    expect(await screen.findByRole("button", { name: "Cancel with no refund" })).toBeTruthy();
  });

  it("lets them back out", async () => {
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel this booking" }));
    await user.click(await screen.findByRole("button", { name: "Keep my booking" }));
    expect(screen.getByRole("button", { name: "Cancel this booking" })).toBeTruthy();
    expect(calls.some((c) => c.url.endsWith("/cancel") && c.method === "POST")).toBe(false);
  });

  it("describes each way a cancelled booking can be refunded", async () => {
    for (const [state, text] of [["pending", /is being processed/], ["failed", /could not send your refund automatically/]] as const) {
      current = { ...base, status: "cancelled", cancelled: { by: "host", at: "x", reason: "Burst pipe", policy: "Moderate", refund_pct: 100 }, refund: { refunded: 0, pending: state === "pending" ? 29563 : 0, failed: state === "failed" ? 29563 : 0, state } };
      const { unmount } = renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
      expect(await screen.findByText(/The host cancelled this booking/)).toBeTruthy();
      expect(screen.getByText(/Burst pipe/)).toBeTruthy();
      expect(screen.getByText(text)).toBeTruthy();
      unmount();
    }
  });
});

describe("host actions on a paid booking", () => {
  const hostSide: BookingItem = {
    ...base, room: "1", price: { nights: 1, hours: 24, accommodation: 25000, cleaning_fee: 0, commission: 3000, commission_percent: 12, host_payable: 22000 },
    guest: { full_name: "Gina Guest", age: 34, member_since: null, email_verified: true, social_links: [], email: "g@example.com", phone: "+61 400 222 333" },
    payout: { state: "scheduled", amount: 22000, eligible_at: "2099-06-16T14:00:00Z" },
  };
  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (init?.method === "POST") return json(200, hostSide);
      return json(200, { items: [hostSide], total: 1, page: 1, page_size: 25, counts: { pending: 0, awaiting_payment: 0, upcoming: 1, past: 0 } });
    });
  });

  it("shows the payout and, when cancelling, insists on a reason and explains the consequences", async () => {
    renderAt("/host/requests", "/host/requests", <RequestsPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /Upcoming/ }));
    expect(await screen.findByText(/Payout scheduled/)).toBeTruthy();
    expect(screen.getAllByText("$220.00").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Cancel this booking" }));
    expect(screen.getByText(/refunded in full, you are not paid/)).toBeTruthy();
    const confirm = screen.getByRole("button", { name: "Cancel booking and refund guest" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    await user.type(screen.getByLabelText(/Reason/), "Burst pipe");
    await user.click(confirm);
    await waitFor(() => expect(bodyOf("/b1/cancel")).toEqual({ reason: "Burst pipe" }));
    expect(await screen.findByText(/Booking cancelled\. The guest has been refunded/)).toBeTruthy();
  });

  it("sends a refund request in cents with the reason", async () => {
    renderAt("/host/requests", "/host/requests", <RequestsPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: /Upcoming/ }));
    await user.click(await screen.findByRole("button", { name: "Ask for a guest refund" }));
    const send = screen.getByRole("button", { name: "Send request" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    await user.type(screen.getByLabelText(/Amount/), "40.50");
    await user.type(screen.getByLabelText(/Why should the guest/), "The heating was broken");
    await user.click(send);
    await waitFor(() => expect(bodyOf("/b1/refund-request")).toEqual({ amount: 4050, reason: "The heating was broken" }));
    expect(await screen.findByText(/An admin will review it/)).toBeTruthy();
  });

  it("shows who cancelled a cancelled booking and offers no further actions", async () => {
    const cancelled = { ...hostSide, status: "cancelled" as const, cancelled: { by: "guest" as const, at: "x", reason: null, policy: "Moderate", refund_pct: 50 }, payout: { state: "scheduled" as const, amount: 11000, eligible_at: "2099-06-16T00:00:00Z" } };
    vi.stubGlobal("fetch", () => json(200, { items: [cancelled], total: 1, page: 1, page_size: 25, counts: { pending: 0, awaiting_payment: 0, upcoming: 0, past: 1 } }));
    renderAt("/host/requests", "/host/requests", <RequestsPage />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: /Past/ }));
    expect(await screen.findByText(/Cancelled by the guest \(50% refunded under Moderate\)/)).toBeTruthy();
    expect(screen.getAllByText("$110.00").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Cancel this booking" })).toBeNull();
  });
});

describe("admin refunds", () => {
  const request: RefundRequestItem = { id: "rr1", booking_id: "b1", amount: 5000, reason: "The heating was broken", status: "pending", note: null, created_at: "2099-01-01T00:00:00Z", reference: "NB-ABCD2345", property: "Beach House", host: "Hannah Host", currency: "AUD", paid: 29563 };
  const failed: RefundRow = { id: "rf1", reference: "NB-ABCD2345", booking_id: "b1", amount: 27500, currency: "AUD", type: "policy", status: "failed", reason: null, error: "Stripe is down", stripe_refund_id: null, host_reversal_amount: 0, created_at: "2099-01-01T00:00:00Z" };
  let decide: () => Promise<Response>;
  beforeEach(() => {
    calls = [];
    decide = () => json(200, { ...request, status: "approved" });
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (url.includes("/decision")) return decide();
      if (url.includes("/retry")) return json(200, { id: "rf1", status: "succeeded", error: null });
      if (url.includes("/admin/refund-requests")) return json(200, [request]);
      if (url.includes("/admin/refunds")) return json(200, [failed]);
      return json(404, {});
    });
  });

  it("shows each request with what was paid and approves with a possibly smaller amount", async () => {
    renderAt("/admin/refunds", "/admin/refunds", <AdminRefundsPage />);
    expect(await screen.findByText(/Asked for/)).toBeTruthy();
    expect(screen.getByText("The heating was broken")).toBeTruthy();
    const amount = screen.getByLabelText(/Amount to refund/) as HTMLInputElement;
    expect(amount.value).toBe("50.00");
    const user = userEvent.setup();
    await user.clear(amount);
    await user.type(amount, "40");
    await user.type(screen.getByLabelText(/Note to the host/), "Fair");
    await user.click(screen.getByRole("button", { name: "Approve and refund" }));
    await waitFor(() => expect(bodyOf("/decision")).toEqual({ decision: "approve", note: "Fair", amount: 4000 }));
  });

  it("shows the server's message when a rejection has no note", async () => {
    decide = () => json(422, { error: { code: "NOTE_REQUIRED", message: "Tell the host why the request was rejected." } });
    renderAt("/admin/refunds", "/admin/refunds", <AdminRefundsPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Tell the host why the request was rejected.")).toBeTruthy();
    expect(bodyOf("/decision")).toEqual({ decision: "reject", note: null });
  });

  it("lists failed refunds with the reason and can retry one", async () => {
    renderAt("/admin/refunds", "/admin/refunds", <AdminRefundsPage />);
    expect(await screen.findByText("Stripe is down")).toBeTruthy();
    expect(screen.getByText("$275.00")).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/rf1/retry") && c.method === "POST")).toBe(true));
  });
});

describe("admin cancellation policies", () => {
  const moderate: CancellationTemplate = { id: "t1", key: "moderate", name: "Moderate", description: "Mid", strictness: 2, active: true, version: 1, tiers: [{ hours_before: 120, refund_pct: 100 }, { hours_before: 24, refund_pct: 50 }] };
  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (init?.method === "PATCH") return json(200, { ...moderate, version: 2 });
      if (init?.method === "POST") return json(201, moderate);
      return json(200, [moderate]);
    });
  });

  it("edits the steps of a policy and says existing bookings are unaffected", async () => {
    renderAt("/admin/cancellation", "/admin/cancellation", <AdminCancellationPage />);
    const user = userEvent.setup();
    await screen.findByText(/version 1/);                                                                       // wait for the policy to load
    const hours = screen.getAllByLabelText(/At least \(hours before check-in\)/)[1] as HTMLInputElement;        // the template's second step
    await user.clear(hours);
    await user.type(hours, "48");
    await user.click(screen.getAllByRole("button", { name: "Save changes" })[0]);
    await waitFor(() => expect(bodyOf("/t1")?.tiers).toEqual([{ hours_before: 120, refund_pct: 100 }, { hours_before: 48, refund_pct: 50 }]));
    expect(await screen.findByText(/existing bookings keep the policy they were made under/)).toBeTruthy();
  });

  it("adds a new policy with its steps", async () => {
    renderAt("/admin/cancellation", "/admin/cancellation", <AdminCancellationPage />);
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Key (letters and numbers)"), "super_flex");
    const addForm = within(screen.getByText("Add a policy").closest("section") as HTMLElement);
    await user.type(addForm.getByLabelText("Name"), "Super flexible");
    await user.click(addForm.getByRole("button", { name: "Add policy" }));
    await waitFor(() => expect(bodyOf("/admin/cancellation-templates")).toMatchObject({ key: "super_flex", name: "Super flexible", strictness: 2, tiers: [{ hours_before: 24, refund_pct: 100 }] }));
  });

  it("switches a policy off", async () => {
    renderAt("/admin/cancellation", "/admin/cancellation", <AdminCancellationPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Switch off" }));
    await waitFor(() => expect(bodyOf("/t1")?.active).toBe(false));
  });
});

describe("admin bookings: cancel and payout controls", () => {
  const confirmed = { ...base, guest: { full_name: "Gina Guest", age: 34, member_since: null, email_verified: true, social_links: [], email: "g@example.com" }, host: { id: "h1", name: "Hannah", email: "h@example.com" },
    payout: { state: "scheduled" as const, amount: 22000, eligible_at: "2099-06-16T14:00:00Z" } };
  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
      if (init?.method === "POST") return json(200, confirmed);
      return json(200, { items: [confirmed], total: 1, page: 1, page_size: 25, counts: { pending: 0, awaiting_payment: 0, upcoming: 1, past: 0 } });
    });
  });

  it("cancels with a chosen refund and a required reason", async () => {
    renderAt("/admin/bookings", "/admin/bookings", <AdminBookingsPage />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Cancel booking" }));
    const go = screen.getAllByRole("button", { name: "Cancel booking" }).at(-1) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    await user.selectOptions(screen.getByLabelText("Refund"), "full");
    await user.type(screen.getByLabelText(/Reason/), "Property condemned");
    await user.click(go);
    await waitFor(() => expect(bodyOf("/b1/cancel")).toEqual({ reason: "Property condemned", refund: "full" }));
  });

  it("puts a payout on hold with a note", async () => {
    renderAt("/admin/bookings", "/admin/bookings", <AdminBookingsPage />);
    const user = userEvent.setup();
    expect(await screen.findByText(/Payout scheduled: \$220\.00/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Hold payout" }));
    await user.type(screen.getByLabelText(/Why is the payout on hold/), "Complaint");
    await user.click(screen.getAllByRole("button", { name: "Hold payout" }).at(-1)!);
    await waitFor(() => expect(bodyOf("/b1/payout-hold")).toEqual({ hold: true, note: "Complaint" }));
  });
});
