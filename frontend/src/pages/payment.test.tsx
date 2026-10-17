import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import type { BookingItem, InvoiceData, PayStart } from "../lib/types";
import { InvoicePage } from "./account/Invoice";
import { PayPage } from "./account/Pay";
import { TripDetailPage } from "./account/Trips";

const stripe = vi.hoisted(() => ({ confirmPayment: vi.fn(), loadStripe: vi.fn() }));
vi.mock("@stripe/stripe-js", () => ({ loadStripe: (key: string) => { stripe.loadStripe(key); return Promise.resolve({}); } }));
vi.mock("@stripe/react-stripe-js", () => ({
  Elements: ({ children }: { children: React.ReactNode }) => <div data-testid="elements">{children}</div>,
  PaymentElement: () => <div data-testid="payment-element" />,
  useStripe: () => ({ confirmPayment: stripe.confirmPayment }),
  useElements: () => ({}),
}));

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const booking: BookingItem = {
  id: "b1", reference: "NB-ABCD2345", status: "accepted", hours: 24, guests: 2, currency: "AUD", timezone: "Australia/Sydney",
  check_in_local: "2099-06-15T14:00", check_out_local: "2099-06-16T14:00", check_in_label: "15 Jun 2:00 PM", check_out_label: "16 Jun 2:00 PM",
  requested_at: "2099-01-01T00:00:00Z", expires_at: null, payment_deadline_at: "2099-01-01T00:30:00Z", seconds_to_respond: null, seconds_to_pay: 1500,
  closed_reason: null, can_withdraw: true, can_pay: true, invoice_number: null,
  property: { id: "p1", name: "Beach House", suburb: "Bondi", state: "NSW", cover_url: null }, unit: { id: "u1", name: "Entire house" }, room: null,
  price: { nights: 1, hours: 24, accommodation: 25000, cleaning_fee: 0, service_fee: 1875, service_fee_percent: 7.5, tax: 2688, tax_name: "GST", tax_rate_percent: 10, tax_inclusive: false, total: 29563 },
  host: { first_name: "Hannah" },
};

const invoice: InvoiceData = {
  number: "INV-000007", issued_at: "2099-01-01T00:10:00Z", total: 29563, currency: "AUD", footer: "Thank you for booking with NearBnB.",
  seller: { name: "NearBnB Pty Ltd", tax_id: "ABN 12 345 678 901", address: "1 Example St" },
  buyer: { name: "Gina Guest", email: "gina@example.com" },
  stay: { reference: "NB-ABCD2345", property: "Beach House", unit: "Entire house", address: "1 Beach Rd, Bondi", check_in: "15 Jun 2:00 PM", check_out: "16 Jun 2:00 PM", timezone: "Australia/Sydney", hours: 24, nights: 1, guests: 2 },
  lines: [{ description: "Accommodation: 1 night at $250.00", amount: 25000 }, { description: "Service fee (7.5%)", amount: 1875 }],
  tax: { name: "GST", rate_percent: 10, amount: 2688, inclusive: false },
  payment: { reference: "ch_123", paid_at: "2099-01-01T00:10:00Z" },
};

function Where() {
  const loc = useLocation();
  return <p data-testid="where">{loc.pathname}{loc.search}</p>;
}

function renderAt(path: string, route: string, ui: React.ReactNode) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={ui} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

let calls: { url: string; method?: string }[] = [];
let current: BookingItem = booking;
let payStart: () => Promise<Response>;
const realStart: PayStart = { booking_id: "b1", client_secret: "pi_1_secret_x", amount: 29563, currency: "AUD", publishable_key: "pk_test_abc", fake: false, seconds_to_pay: 1500 };

beforeEach(() => {
  calls = [];
  current = booking;
  stripe.confirmPayment.mockReset();
  stripe.loadStripe.mockReset();
  payStart = () => json(200, realStart);
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method });
    if (url.endsWith("/pay/sync")) return json(200, { outcome: "confirmed", booking: current });
    if (url.endsWith("/pay/dev-complete")) return json(200, { outcome: "confirmed", booking: current });
    if (url.endsWith("/pay")) return payStart();
    if (url.includes("/invoice")) return json(200, invoice);
    if (url.includes("/me/bookings/b1")) return json(200, current);
    return json(404, { error: { code: "NOT_FOUND", message: "no" } });
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("pay page", () => {
  it("shows the price and the card form, and loads Stripe with the publishable key from the server", async () => {
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    expect(await screen.findByTestId("payment-element")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Pay \$295\.63/ })).toBeTruthy();
    expect(screen.getByText(/Time left to pay/)).toBeTruthy();
    expect(screen.getByLabelText("Price breakdown")).toBeTruthy();
    expect(stripe.loadStripe).toHaveBeenCalledWith("pk_test_abc");
    expect(calls.filter((c) => c.url.endsWith("/pay") && c.method === "POST")).toHaveLength(1);
  });

  it("confirms with Stripe, asks the server to check the payment, then opens the trip", async () => {
    stripe.confirmPayment.mockResolvedValue({});
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Pay \$295\.63/ }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/account/trips/b1?paid=1"));
    const args = stripe.confirmPayment.mock.calls[0][0];
    expect(args.redirect).toBe("if_required");
    expect(args.confirmParams.return_url).toMatch(/\/account\/trips\/b1\?paid=1$/);
    expect(calls.some((c) => c.url.endsWith("/pay/sync") && c.method === "POST")).toBe(true);
  });

  it("shows the card error and stays on the form so another card can be tried", async () => {
    stripe.confirmPayment.mockResolvedValue({ error: { message: "Your card was declined." } });
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    await userEvent.setup().click(await screen.findByRole("button", { name: /Pay \$295\.63/ }));
    expect(await screen.findByText("Your card was declined.")).toBeTruthy();
    expect(screen.queryByTestId("where")).toBeNull();
    expect(calls.some((c) => c.url.endsWith("/pay/sync"))).toBe(false);
    expect((screen.getByRole("button", { name: /Pay \$295\.63/ }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("explains when the server has no publishable key instead of showing a broken form", async () => {
    payStart = () => json(200, { ...realStart, publishable_key: null });
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    expect(await screen.findByText(/publishable key is missing/)).toBeTruthy();
    expect(screen.queryByTestId("payment-element")).toBeNull();
  });

  it("in development without Stripe keys offers a clearly labelled simulated payment", async () => {
    payStart = () => json(200, { ...realStart, fake: true, publishable_key: null, client_secret: "pi_fake_secret_fake" });
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    expect(await screen.findByText(/Development mode/)).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("button", { name: /Simulate paying \$295\.63/ }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/account/trips/b1?paid=1"));
    expect(calls.some((c) => c.url.endsWith("/pay/dev-complete") && c.method === "POST")).toBe(true);
    expect(stripe.confirmPayment).not.toHaveBeenCalled();
  });

  it("shows the server's reason when payment can no longer be started", async () => {
    payStart = () => json(409, { error: { code: "PAYMENT_EXPIRED", message: "The time to pay has run out." } });
    renderAt("/account/trips/b1/pay", "/account/trips/:bookingId/pay", <PayPage />);
    expect(await screen.findByText(/The time to pay has run out/)).toBeTruthy();
    expect(screen.queryByTestId("payment-element")).toBeNull();
  });
});

describe("trip page and payment", () => {
  it("offers Pay now with the total while a request is accepted", async () => {
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    const link = await screen.findByRole("link", { name: /Pay now · \$295\.63/ });
    expect(link.getAttribute("href")).toBe("/account/trips/b1/pay");
    expect(calls.some((c) => c.url.endsWith("/pay/sync"))).toBe(false);          // nothing to check until the guest has paid
  });

  it("tells the guest why the last payment failed and lets them retry", async () => {
    current = { ...booking, payment: { status: "failed", failure_message: "Your card has insufficient funds." } };
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    expect(await screen.findByText(/Your card has insufficient funds/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Pay now/ })).toBeTruthy();
  });

  it("on return from the payment form it asks the server to check with Stripe, and then shows the confirmation and receipt", async () => {
    current = { ...booking, status: "payment_pending", can_withdraw: false };
    renderAt("/account/trips/b1?paid=1", "/account/trips/:bookingId", <TripDetailPage />);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/pay/sync") && c.method === "POST")).toBe(true));
    expect(await screen.findByText(/Checking your payment with Stripe/)).toBeTruthy();
  });

  it("shows a confirmed stay with the host's details, instructions and a link to the receipt", async () => {
    current = {
      ...booking, status: "confirmed", can_pay: false, can_withdraw: false, seconds_to_pay: null, invoice_number: "INV-000007",
      host: { first_name: "Hannah", name: "Hannah Host", email: "h@example.com", phone: "+61 400 111 222" }, checkin_instructions: "Key is in the lockbox: 4321",
    };
    renderAt("/account/trips/b1", "/account/trips/:bookingId", <TripDetailPage />);
    expect(await screen.findByText(/Your stay is confirmed/)).toBeTruthy();
    expect(screen.getByText("Key is in the lockbox: 4321")).toBeTruthy();
    expect(screen.getByRole("link", { name: "View receipt and tax invoice" }).getAttribute("href")).toBe("/account/trips/b1/invoice");
    expect(screen.queryByRole("link", { name: /Pay now/ })).toBeNull();
  });
});

describe("invoice page", () => {
  it("prints the seller, buyer, stay, lines, tax and total", async () => {
    renderAt("/account/trips/b1/invoice", "/account/trips/:bookingId/invoice", <InvoicePage />);
    expect(await screen.findByRole("article", { name: "Tax invoice" })).toBeTruthy();
    expect(screen.getByText("INV-000007")).toBeTruthy();
    expect(screen.getByText("NearBnB Pty Ltd")).toBeTruthy();
    expect(screen.getByText("ABN 12 345 678 901")).toBeTruthy();
    expect(screen.getByText("Gina Guest")).toBeTruthy();
    expect(screen.getByText("Accommodation: 1 night at $250.00")).toBeTruthy();
    expect(screen.getByText("$18.75")).toBeTruthy();
    expect(screen.getByText(/GST \(10%\)/)).toBeTruthy();
    expect(screen.getByText("$26.88")).toBeTruthy();
    expect(screen.getAllByText("$295.63").length).toBeGreaterThan(0);
    expect(screen.queryByText(/still placeholders/)).toBeNull();
    expect(screen.getByRole("button", { name: "Print or save as PDF" })).toBeTruthy();
  });

  it("reminds an admin when the seller details are still placeholders", async () => {
    vi.stubGlobal("fetch", () => json(200, { ...invoice, seller: { ...invoice.seller, tax_id: "ABN to be configured" } }));
    renderAt("/account/trips/b1/invoice", "/account/trips/:bookingId/invoice", <InvoicePage />);
    expect(await screen.findByText(/still placeholders/)).toBeTruthy();
  });

  it("says no invoice exists before payment", async () => {
    vi.stubGlobal("fetch", () => json(404, { error: { code: "NOT_FOUND", message: "Invoice not found." } }));
    renderAt("/account/trips/b1/invoice", "/account/trips/:bookingId/invoice", <InvoicePage />);
    expect(await screen.findByText("No invoice yet")).toBeTruthy();
  });
});
