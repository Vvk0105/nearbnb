import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import indexHtml from "../../index.html?raw";
import App from "../App";
import { AppShell } from "../components/AppShell";
import { AuthProvider } from "../lib/auth";
import { UnitEditor } from "./host/PropertyPages";

const appCss = readFileSync("src/index.css", "utf-8");   // a "?raw" CSS import comes back empty under vitest

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("phone basics", () => {
  it("the page tells phones to use their real width", () => {
    expect(indexHtml).toMatch(/<meta\s+name="viewport"\s+content="width=device-width,\s*initial-scale=1\.0"\s*\/?>/);
  });

  it("text boxes are 16px on small screens (so iPhones do not zoom in when one is tapped) and tall enough for a thumb on touch screens", () => {
    expect(appCss).toMatch(/@media \(max-width: 640px\)\s*{[^}]*font-size: 16px/);
    expect(appCss).toMatch(/@media \(pointer: coarse\)\s*{[^}]*min-height: 2\.75rem/);
  });

  it("date and time boxes cannot push out past their card", () => {
    expect(appCss).toMatch(/input\[type="date"\][^{]*{[^}]*min-width: 0[^}]*max-width: 100%/);
  });
});

describe("host forms on a phone", () => {
  it("the unit's price and size boxes stack on a phone and sit side by side from a tablet up", async () => {
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const unit = {
        id: "u1", property_id: "p1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 2000, capacity: 4, quantity: 1,
        bedrooms: null, bathrooms: null, beds: null, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00", status: "approved", review_note: null,
        amenities: [], rooms: [{ id: "r1", label: "1" }], photos: [],
      };
      if (url.endsWith("/v1/host") && method === "GET") return json(200, { state: "active", id: "h1", can_create_listings: true, can_submit_listings: true, steps: [], stripe: {}, documents: [], application: {} });
      if (url.endsWith("/v1/host/properties/p1")) return json(200, { id: "p1", name: "Home", description: "", rules: "", type_id: null, type_name: null, address_line1: "", address_line2: "", suburb: "", state: "NSW", postcode: "", country: "AU", latitude: null, longitude: null, timezone: "Australia/Sydney", status: "approved", photos: [], units: [unit], checklist: [], pending_revisions: [] });
      if (url.includes("/v1/host/units/u1/calendar")) return json(200, { timezone: "Australia/Sydney", rooms: [], entries: [] });
      if (url.includes("/v1/host/units/u1/ical")) return json(200, { eligible: false, reason: "n/a" });
      if (url.endsWith("/v1/meta/listing")) return json(200, { property_types: [], amenities: [] });
      return json(404, { error: { code: "NOT_FOUND", message: "x" } });
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/host/properties/p1/units/u1"]}>
          <Routes><Route path="/host/properties/:propertyId/units/:unitId" element={<UnitEditor />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const price = await screen.findByLabelText(/Price per/);
    const grid = price.closest(".grid")!;
    expect(grid.className).toContain("grid-cols-1");
    expect(grid.className).toContain("sm:grid-cols-2");
    expect(grid.className.split(" ")).not.toContain("grid-cols-2");   // a bare grid-cols-2 would squeeze two boxes into 360px
  });
});

describe("the host and admin top bar on a phone", () => {
  it("keeps the log out button findable by name when only its icon is shown", async () => {
    vi.stubGlobal("fetch", () => json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } }));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><AuthProvider><AppShell area="Host" groups={[]} counts={{}} homeTo="/host" title="Dashboard" /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    const button = await screen.findByRole("button", { name: "Log out" });
    expect(button.getAttribute("aria-label")).toBe("Log out");
    expect(button.querySelector("span")!.className).toContain("hidden");     // the word is hidden on a phone, the icon stays
    expect(button.querySelector("span")!.className).toContain("sm:inline");  // and it comes back from a tablet up
  });
});

describe("a room card on the property page, on a phone", () => {
  it("gives the description the full width of the card, with the price above and a full-width button below", async () => {
    const unit = {
      id: "u1", property_id: "p1", name: "Entire House", description: "Enjoy a comfortable stay in this spacious entire house.", price_per_24h: 1000000, currency: "AUD",
      cleaning_fee: 200000, capacity: 8, quantity: 1, bedrooms: 4, bathrooms: 4, beds: 4, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00", status: "approved",
      review_note: null, amenities: [{ id: "a1", name: "Balcony" }], rooms: [{ id: "r1", label: "1" }], photos: [],
      rules: { interval_minutes: 60, step_hours: 24, max_stay_hours: 168, min_lead_hours: 2, max_advance_days: 180 },
    };
    const page = {
      id: "p1", name: "Beach House", description: "", rules: "", type_id: null, type_name: null, address_line1: "", address_line2: "", suburb: "Bondi", state: "NSW",
      postcode: "", country: "AU", latitude: null, longitude: null, timezone: "Australia/Sydney", status: "approved", photos: [], units: [unit], host_first_name: "Hannah", rating: null, cancellation: null,
    };
    vi.stubGlobal("fetch", (url: string) => {
      if (url.endsWith("/v1/me")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
      if (url.includes("/v1/properties/p1/reviews")) return json(200, { items: [], rating: null, total: 0 });
      if (url.endsWith("/v1/properties/p1")) return json(200, page);
      return json(404, { error: { code: "NOT_FOUND", message: "x" } });
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/property/p1"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    const description = await screen.findByText("Enjoy a comfortable stay in this spacious entire house.");
    const card = description.closest("section")!;
    expect(description.parentElement).toBe(card);                                        // a direct child of the card: nothing narrows it
    const heading = screen.getByRole("heading", { name: "Entire House" }).parentElement!.parentElement!;
    expect(heading.className).toContain("flex-col");                                     // name and price stack on a phone...
    expect(heading.className).toContain("sm:flex-row");                                  // ...and sit side by side from a tablet up
    const button = within(card).getByRole("button", { name: /(Check|Hide) availability/ });
    expect(button.className).toContain("w-full");
    expect(button.className).toContain("sm:w-auto");
  });
});
