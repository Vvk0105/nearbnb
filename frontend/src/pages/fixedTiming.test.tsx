import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "../lib/auth";
import type { PublicUnit } from "../lib/types";
import { AvailabilityPanel, fixedTiming } from "../components/availability/AvailabilityPanel";
import { PropertyCard } from "../components/PropertyCard";
import { UnitEditor } from "./host/PropertyPages";
import App from "../App";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

type Call = { method: string; url: string; body: Record<string, unknown> | undefined };
let calls: Call[] = [];

const baseUnit = {
  id: "u1", property_id: "p1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 2000,
  capacity: 4, quantity: 1, bedrooms: null, bathrooms: null, beds: null, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00",
  status: "draft", review_note: null, amenities: [], rooms: [{ id: "r1", label: "1" }], photos: [],
};
const FIXED = { stay_mode: "fixed" as const, fixed_checkin_time: "12:00", fixed_checkout_time: "11:30" };
const hostInfo = {
  state: "active", id: "h1", review_status: "approved", review_note: null, editable: false, application: {}, documents: [], steps: [],
  stripe: { connected: true, details_submitted: true, payouts_enabled: true, transfers_active: true, ready: true, requirements: [], dev_mode: false },
  can_create_listings: true, can_submit_listings: true,
};
const property = (unit: object) => ({
  id: "p1", name: "Beach House", description: "", rules: "", type_id: null, type_name: null, address_line1: "", address_line2: "", suburb: "", state: "NSW",
  postcode: "", country: "AU", latitude: null, longitude: null, timezone: "Australia/Sydney", status: "draft", photos: [], units: [unit], checklist: [], pending_revisions: [],
});

function openEditor(unit: object) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    if (url.endsWith("/v1/host") && method === "GET") return json(200, hostInfo);
    if (url.endsWith("/v1/host/properties/p1") && method === "GET") return json(200, property(unit));
    if (url.endsWith("/v1/host/units/u1") && method === "PATCH") return json(200, { ...property(unit), result: { applied: [], pending_review: [] } });
    if (url.includes("/v1/host/units/u1/calendar")) return json(200, { timezone: "Australia/Sydney", rooms: [{ id: "r1", label: "1" }], entries: [] });
    if (url.includes("/v1/host/units/u1/ical")) return json(200, { eligible: false, reason: "n/a" });
    if (url.endsWith("/v1/meta/listing")) return json(200, { property_types: [], amenities: [] });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/host/properties/p1/units/u1"]}>
        <Routes><Route path="/host/properties/:propertyId/units/:unitId" element={<UnitEditor />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const patchBody = () => calls.find((c) => c.method === "PATCH" && c.url.endsWith("/host/units/u1"))!.body!;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("unit form: flexible or fixed timing", () => {
  it("a new unit is flexible: it shows the check-in window and no fixed times", async () => {
    openEditor(baseUnit);
    expect(await screen.findByLabelText("Earliest check-in time")).toBeTruthy();
    expect(screen.getByLabelText("Latest check-in time")).toBeTruthy();
    expect(screen.queryByLabelText("Check-out time")).toBeNull();
    expect((screen.getByRole("radio", { name: /Flexible/ }) as HTMLInputElement).checked).toBe(true);
    expect(screen.getByLabelText("Price per 24 hours ($)")).toBeTruthy();
  });

  it("choosing Fixed swaps the window for a check-in time and a check-out time, and prices per night", async () => {
    const u = userEvent.setup();
    openEditor(baseUnit);
    await u.click(await screen.findByRole("radio", { name: /Fixed/ }));
    expect(screen.queryByLabelText("Earliest check-in time")).toBeNull();
    const checkin = screen.getByLabelText("Check-in time") as HTMLInputElement;
    const checkout = screen.getByLabelText("Check-out time") as HTMLInputElement;
    expect([checkin.value, checkout.value]).toEqual(["12:00", "11:00"]);          // sensible starting values
    expect(screen.getByLabelText("Price per night ($)")).toBeTruthy();
    fireChange(checkin, "12:00");
    await u.clear(checkout);
    await u.type(checkout, "11:30");
    await u.click(screen.getByRole("button", { name: "Save unit" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(patchBody()).toMatchObject({ stay_mode: "fixed", fixed_checkin_time: "12:00", fixed_checkout_time: "11:30" });
  });

  it("an existing fixed unit opens on Fixed with its times", async () => {
    openEditor({ ...baseUnit, ...FIXED });
    expect((await screen.findByLabelText("Check-in time") as HTMLInputElement).value).toBe("12:00");
    expect((screen.getByLabelText("Check-out time") as HTMLInputElement).value).toBe("11:30");
    expect((screen.getByRole("radio", { name: /Fixed/ }) as HTMLInputElement).checked).toBe(true);
  });

  it("saving a flexible unit sends no fixed times", async () => {
    const u = userEvent.setup();
    openEditor(baseUnit);
    await u.click(await screen.findByRole("button", { name: "Save unit" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(patchBody().stay_mode).toBe("flexible");
    expect(patchBody()).not.toHaveProperty("fixed_checkin_time");
    expect(patchBody()).not.toHaveProperty("fixed_checkout_time");
  });

  it("tells the host timing can only change while there are no upcoming bookings", async () => {
    openEditor(baseUnit);
    expect(await screen.findByText(/only while the unit has no upcoming bookings/)).toBeTruthy();
  });
});

function fireChange(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

// ---------- the guest's availability panel ----------

const publicUnit = (extra: object = {}): PublicUnit => ({
  ...baseUnit, status: "approved",
  rules: { interval_minutes: 60, step_hours: 24, max_stay_hours: 168, min_lead_hours: 2, max_advance_days: 180 }, ...extra,
}) as PublicUnit;

function openPanel(unit: PublicUnit) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method: init?.method ?? "GET", url, body });
    if (url.endsWith("/me")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "Please log in" } });
    if (url.includes("/availability/calendar")) return json(200, { timezone: "Australia/Sydney", year: 2099, month: 6, days: [] });
    if (url.includes("/availability/day")) return json(200, { timezone: "Australia/Sydney", date: "2099-06-15", rooms: 1, occupied: [] });
    if (url.endsWith("/quote")) {
      const checkin = String(body?.checkin_local);
      return json(200, {
        unit_id: "u1", available: true, timezone: "Australia/Sydney", hours: body?.hours, guests: 2, currency: "AUD", rooms_free: 1,
        checkin_local: checkin, checkout_local: "2099-06-16T11:30", checkin_label: "15 Jun 12:00 PM", checkout_label: "16 Jun 11:30 AM",
        price: { nights: 1, hours: 24, accommodation: 25000, cleaning_fee: 2000, service_fee: 1875, service_fee_percent: 7.5, tax: 0, tax_name: null, tax_rate_percent: 0, tax_inclusive: false, total: 28875 },
        response_minutes: 30, alternatives: [],
      });
    }
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><AuthProvider><AvailabilityPanel unit={unit} timezone="Australia/Sydney" initial={{ date: "2099-06-15" }} /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("availability panel for a fixed unit", () => {
  it("shows the fixed times instead of an hour picker, and asks for nights, not hours", async () => {
    openPanel(publicUnit(FIXED));
    expect(await screen.findByText(/check in 12:00 PM/)).toBeTruthy();
    expect(screen.getByText(/check out 11:30 AM/)).toBeTruthy();
    expect(screen.queryByLabelText("Check-in time")).toBeNull();
    const stay = screen.getByLabelText("Nights");
    expect(within(stay).getByRole("option", { name: "1 night" })).toBeTruthy();
    expect(within(stay).getByRole("option", { name: "3 nights" })).toBeTruthy();
    expect(within(stay).queryByRole("option", { name: /hours/ })).toBeNull();
  });

  it("asks the server about the date at the fixed check-in time, for a whole number of nights", async () => {
    const u = userEvent.setup();
    openPanel(publicUnit(FIXED));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/quote"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/quote"))!.body).toMatchObject({ checkin_local: "2099-06-15T12:00", hours: 24 });
    await u.selectOptions(screen.getByLabelText("Nights"), "2");
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/quote")).some((c) => c.body?.hours === 48)).toBe(true));
    expect(await screen.findByText(/Check in 15 Jun 12:00 PM · Check out 16 Jun 11:30 AM/)).toBeTruthy();
  });
});

describe("availability panel for a flexible unit (unchanged)", () => {
  it("still offers the check-in hour and the stay in hours", async () => {
    openPanel(publicUnit());
    expect(await screen.findByLabelText("Check-in time")).toBeTruthy();
    expect(within(screen.getByLabelText("Stay")).getByRole("option", { name: "48 hours (2 nights)" })).toBeTruthy();
    expect(screen.queryByText(/Fixed times/)).toBeNull();
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/quote"))).toBe(true));
    expect(calls.find((c) => c.url.endsWith("/quote"))!.body).toMatchObject({ checkin_local: "2099-06-15T14:00", hours: 24 });
  });
});

describe("helpers and cards", () => {
  it("fixedTiming needs the mode and both times; anything else is flexible", () => {
    expect(fixedTiming(FIXED)).toBe(true);
    expect(fixedTiming({ stay_mode: "fixed", fixed_checkin_time: "12:00", fixed_checkout_time: null })).toBe(false);
    expect(fixedTiming({ stay_mode: "flexible", fixed_checkin_time: "12:00", fixed_checkout_time: "11:30" })).toBe(false);
    expect(fixedTiming({})).toBe(false);                                            // units from before this feature
  });

  it("a search card says per night only when every unit is fixed", () => {
    const item = (modes: (string | undefined)[]) => ({
      id: "p1", name: "Beach House", type_name: null, suburb: "Bondi", state: "NSW", cover_url: null, thumb_url: null, currency: "AUD", min_price: 25000, distance_km: null,
      units: modes.map((m, i) => ({ id: `u${i}`, name: "Unit", price_per_24h: 25000, capacity: 2, stay_mode: m })),
    });
    const { unmount } = render(<MemoryRouter><PropertyCard item={item(["fixed"])} carry="" /></MemoryRouter>);
    expect(screen.getByText("/ night")).toBeTruthy();
    unmount();
    const flex = render(<MemoryRouter><PropertyCard item={item(["fixed", "flexible"])} carry="" /></MemoryRouter>);
    expect(flex.container.textContent).toContain("/ 24 hours");
    flex.unmount();
    render(<MemoryRouter><PropertyCard item={item([undefined])} carry="" /></MemoryRouter>);
    expect(screen.getByText(/\/ 24 hours/)).toBeTruthy();
  });
});

describe("the unit card on the property page", () => {
  it("names each unit's timing and prices a fixed unit per night, so guests know before they ask", async () => {
    const units = [
      { ...publicUnit(FIXED), id: "u1", name: "Garden room" },
      { ...publicUnit(), id: "u2", name: "Whole house" },
    ];
    const page = { ...property(units[0]), status: "approved", units, host_first_name: "Hannah", rating: null, cancellation: null };
    vi.stubGlobal("fetch", (url: string) => {
      if (url.endsWith("/v1/me")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
      if (url.includes("/v1/properties/p1/reviews")) return json(200, { items: [], rating: null, total: 0 });
      if (url.endsWith("/v1/properties/p1")) return json(200, page);
      if (url.includes("/availability/calendar")) return json(200, { timezone: "Australia/Sydney", year: 2099, month: 6, days: [] });
      return json(404, { error: { code: "NOT_FOUND", message: "x" } });
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/property/p1"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Fixed times: check in 12:00 PM, check out 11:30 AM")).toBeTruthy();
    expect(screen.getByText("Flexible check-in 8:00 AM to 10:00 PM")).toBeTruthy();
    expect(screen.getByText(/^per night/)).toBeTruthy();
    expect(screen.getByText(/^per 24 hours/)).toBeTruthy();
  });
});
