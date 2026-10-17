import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { dayLabel, timeLabel, timeOptions } from "../../lib/format";
import type { PublicUnit } from "../../lib/types";
import { AvailabilityPanel } from "./AvailabilityPanel";
import { segmentFor } from "./DayTimeline";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const unit: PublicUnit = {
  id: "u1", property_id: "p1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 2000, capacity: 4,
  quantity: 1, bedrooms: null, bathrooms: null, beds: null, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00", status: "approved",
  review_note: null, amenities: [], rooms: [{ id: "r1", label: "1" }], photos: [],
  rules: { interval_minutes: 60, step_hours: 24, max_stay_hours: 168, min_lead_hours: 2, max_advance_days: 180 },
};

describe("segmentFor places occupied time on a 24-hour bar", () => {
  it("same-day range", () => {
    const s = segmentFor("2099-06-15", "2099-06-15T06:00", "2099-06-15T18:00");
    expect(s.left).toBeCloseTo(25);
    expect(s.width).toBeCloseTo(50);
  });
  it("a stay that began on an earlier day runs from the left edge", () => {
    const s = segmentFor("2099-06-16", "2099-06-15T14:00", "2099-06-16T14:00");
    expect(s.left).toBe(0);
    expect(s.width).toBeCloseTo((14 / 24) * 100);
  });
  it("a stay that ends on a later day runs to the right edge", () => {
    const s = segmentFor("2099-06-15", "2099-06-15T14:00", "2099-06-16T14:00");
    expect(s.left + s.width).toBeCloseTo(100);
  });
  it("a range covering the whole day fills the bar", () => {
    expect(segmentFor("2099-06-15", "2099-06-14T10:00", "2099-06-17T10:00")).toEqual({ left: 0, width: 100 });
  });
});

describe("formatting helpers", () => {
  it("time options follow the check-in interval and window", () => {
    expect(timeOptions(60, "08:00", "11:00")).toEqual(["08:00", "09:00", "10:00", "11:00"]);
    expect(timeOptions(30, "08:00", "09:00")).toEqual(["08:00", "08:30", "09:00"]);
    expect(timeOptions(60, "08:30", "10:00")).toEqual(["09:00", "10:00"]);   // never offers a time before the window opens
  });
  it("labels", () => {
    expect(timeLabel("00:00")).toBe("12:00 AM");
    expect(timeLabel("12:00")).toBe("12:00 PM");
    expect(timeLabel("14:30")).toBe("2:30 PM");
    expect(dayLabel("2099-06-15")).toMatch(/15 Jun/);
  });
});

describe("AvailabilityPanel", () => {
  let calls: { url: string; body?: { checkin_local: string; hours: number; guests: number } }[] = [];

  beforeEach(() => {
    calls = [];
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
      calls.push({ url, body });
      if (url.includes("/availability/calendar")) {
        const days = Array.from({ length: 30 }, (_, i) => ({
          date: `2099-06-${String(i + 1).padStart(2, "0")}`,
          status: i + 1 === 10 ? "partial" : i + 1 === 11 ? "unavailable" : i + 1 <= 3 ? "past" : "available",
        }));
        return json(200, { timezone: "Australia/Sydney", year: 2099, month: 6, days });
      }
      if (url.includes("/availability/day")) {
        const date = new URL(url, "http://x").searchParams.get("date");
        return json(200, {
          timezone: "Australia/Sydney", date, rooms: 1,
          occupied: date === "2099-06-20" ? [{ start_local: "2099-06-20T10:00", end_local: "2099-06-21T10:00", kind: "booked",
            label: "Someone is staying from 20 Jun 10:00 AM until 21 Jun 10:00 AM." }] : [],
        });
      }
      if (url.endsWith("/quote")) {
        if (body!.checkin_local === "2099-06-20T14:00") {
          return json(200, {
            available: false, code: "SLOT_UNAVAILABLE", message: "Someone is staying from 20 Jun 10:00 AM until 21 Jun 10:00 AM.",
            timezone: "Australia/Sydney", hours: body!.hours, guests: body!.guests,
            alternatives: [{ checkin_local: "2099-06-22T10:00", checkout_local: "2099-06-23T10:00", label: "22 Jun 10:00 AM to 23 Jun 10:00 AM" }],
          });
        }
        return json(200, {
          available: true, alternatives: [], timezone: "Australia/Sydney", hours: body!.hours, guests: body!.guests, currency: "AUD", rooms_free: 1,
          checkin_local: body!.checkin_local, checkout_local: "x", checkin_label: "22 Jun 10:00 AM", checkout_label: "23 Jun 10:00 AM",
          price: { nights: 1, hours: 24, accommodation: 25000, cleaning_fee: 2000, service_fee: 1875, service_fee_percent: 7.5, tax: 2888,
            tax_name: "GST", tax_rate_percent: 10, tax_inclusive: false, total: 31763 },
        });
      }
      return json(404, {});
    });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const renderPanel = (initial = {}) =>
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AvailabilityPanel unit={unit} timezone="Australia/Sydney" initial={initial} /></QueryClientProvider>);

  it("asks for a date before showing any answer, and never lets you request yet", async () => {
    renderPanel();
    expect(await screen.findByText(/Choose a check-in date/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Request to book" }) as HTMLButtonElement).disabled).toBe(true);
    expect(calls.some((c) => c.url.endsWith("/quote"))).toBe(false);
  });

  it("colours days by availability and disables the past", async () => {
    renderPanel({ date: "2099-06-15" });
    expect(await screen.findByRole("button", { name: /10 Jun, partly available/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /11 Jun, fully booked/ })).toBeTruthy();
    expect((screen.getByRole("button", { name: /2 Jun, in the past/ }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: /15 Jun, available/ }) as HTMLButtonElement).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows the price breakdown for an available period", async () => {
    renderPanel({ date: "2099-06-15", time: "14:00", nights: 1, guests: 2 });
    const breakdown = await screen.findByRole("group", { hidden: true }).catch(() => null);
    expect(breakdown).toBeNull(); // (the breakdown is a <dl>, not a group; checked below by its label)
    const dl = await screen.findByLabelText("Price breakdown");
    expect(within(dl).getByText("Total").nextSibling?.textContent).toBe("$317.63");
    expect(within(dl).getByText(/GST \(10%\)/)).toBeTruthy();
    expect(within(dl).getByText("Cleaning fee")).toBeTruthy();
    expect(screen.getByText(/Check in 22 Jun 10:00 AM/)).toBeTruthy();
    const quoteCall = calls.find((c) => c.url.endsWith("/quote"))!;
    expect(quoteCall.body).toEqual({ checkin_local: "2099-06-15T14:00", hours: 24, guests: 2 });
  });

  it("explains an unavailable period and lets you jump to an alternative", async () => {
    renderPanel({ date: "2099-06-20", time: "14:00" });
    expect(await screen.findByText("Not available")).toBeTruthy();
    expect(screen.getAllByText(/Someone is staying from 20 Jun 10:00 AM until 21 Jun 10:00 AM\./).length).toBeGreaterThan(0);

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: new RegExp(`${dayLabel("2099-06-22")}, 10:00 AM`) }));
    expect(await screen.findByLabelText("Price breakdown")).toBeTruthy();
    expect((screen.getByLabelText("Check-in date") as HTMLInputElement).value).toBe("2099-06-22");
    expect((screen.getByLabelText("Check-in time") as HTMLSelectElement).value).toBe("10:00");
    expect(calls.filter((c) => c.url.endsWith("/quote")).at(-1)!.body!.checkin_local).toBe("2099-06-22T10:00");
  });

  it("the timeline shows the occupied range and its message for the chosen day", async () => {
    renderPanel({ date: "2099-06-20" });
    expect(await screen.findAllByTestId("occupied-segment")).toHaveLength(1);
    expect(screen.getAllByText(/Someone is staying from 20 Jun/).length).toBeGreaterThan(0);
  });

  it("offers only valid check-in times, stay lengths and guest counts for this unit", async () => {
    renderPanel({ date: "2099-06-15" });
    const time = (await screen.findByLabelText("Check-in time")) as HTMLSelectElement;
    const options = Array.from(time.options).map((o) => o.value);
    expect(options[0]).toBe("08:00");
    expect(options.at(-1)).toBe("22:00");
    expect(options).toHaveLength(15);
    const stay = screen.getByLabelText("Stay") as HTMLSelectElement;
    expect(stay.options).toHaveLength(7);                      // 168 hours max = 7 nights
    expect(stay.options[1].textContent).toMatch(/48 hours/);
    const guests = screen.getByLabelText(/Guests/) as HTMLInputElement;
    expect(guests.max).toBe("4");
    const user = userEvent.setup();
    await user.clear(guests);
    await user.type(guests, "9");
    expect(guests.value).toBe("4");                            // clamped to capacity
  });
});
