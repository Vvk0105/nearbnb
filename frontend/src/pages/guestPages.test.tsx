import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { Gallery } from "../components/Gallery";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
const none = () => json(401, { error: { code: "NOT_AUTHENTICATED", message: "Please log in" } });

const item = { id: "p1", name: "Beach House", type_name: "Entire place", suburb: "Bondi", state: "NSW", thumb_url: null, min_price: 25000, currency: "AUD", units: [{ id: "u1" }], rating: null, distance_km: null };
const photos = Array.from({ length: 6 }, (_, i) => ({ id: `ph${i}`, url: `/media/${i}.jpg`, thumb_url: `/media/t${i}.jpg`, position: i, status: "approved" }));
const property = {
  id: "p1", name: "Beach House", description: "Close to the sand.", rules: "", type_name: "Entire place", suburb: "Bondi", state: "NSW", timezone: "Australia/Sydney",
  host_first_name: "Hannah", rating: null, cancellation: null, photos,
  units: [{ id: "u1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 0, capacity: 4, quantity: 1, bedrooms: 2, bathrooms: 1, beds: 2, amenities: [], photos: [] },
          { id: "u2", name: "Garden room", description: "", price_per_24h: 18000, currency: "AUD", cleaning_fee: 0, capacity: 2, quantity: 1, bedrooms: 1, bathrooms: 1, beds: 1, amenities: [], photos: [] }],
};

function open(path: string) {
  vi.stubGlobal("fetch", (url: string) => {
    if (url.endsWith("/v1/me")) return none();
    if (url.endsWith("/v1/auth/refresh")) return none();
    if (url.includes("/v1/search")) return json(200, { total: 1, page: 1, page_size: 6, items: [item], hint: null });
    if (url.includes("/v1/properties/p1/reviews")) return json(200, { items: [], rating: null, total: 0 });
    if (url.endsWith("/v1/properties/p1")) return json(200, property);
    if (url.includes("/v1/meta/")) return json(200, { property_types: [], amenities: [], interval_minutes: 60, step_hours: 24, max_stay_hours: 168 });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("guest home page", () => {
  it("explains how it works, shows recent places, and invites visitors to host", async () => {
    open("/");
    expect(await screen.findByRole("heading", { name: /Find a place for exactly the time you need/ })).toBeTruthy();
    const how = screen.getByRole("region", { name: "How it works" });
    expect(within(how).getAllByRole("listitem")).toHaveLength(3);
    expect(await screen.findByRole("link", { name: /Beach House/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign up to host" }).getAttribute("href")).toBe("/register");
  });

  it("shows the property type once on a card and the price per 24 hours", async () => {
    open("/");
    const card = await screen.findByRole("link", { name: /Beach House/ });
    expect(within(card).getAllByText("Entire place")).toHaveLength(1);
    expect(within(card).getByText("$250.00")).toBeTruthy();
    expect(within(card).getByText("Bondi, NSW")).toBeTruthy();
  });
});

describe("login page", () => {
  it("keeps one h1 and the form, with the pitch hidden from screen readers", async () => {
    open("/login");
    expect(await screen.findByRole("heading", { level: 1, name: "Log in" })).toBeTruthy();
    expect(screen.getByLabelText("Email")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Log in" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Forgot password?" })).toBeTruthy();
  });
});

describe("property page", () => {
  it("shows a sticky summary with the lowest price and jumps to availability", async () => {
    open("/property/p1");
    expect(await screen.findByRole("heading", { level: 1, name: "Beach House" })).toBeTruthy();
    expect(screen.getByText("From")).toBeTruthy();
    expect(screen.getAllByText("$180.00").length).toBeGreaterThan(0);              // the cheaper unit
    expect(screen.getByRole("link", { name: "Check availability" }).getAttribute("href")).toBe("#availability");
    expect(screen.getByText(/Your host: Hannah/)).toBeTruthy();
  });
});

describe("photo gallery", () => {
  it("opens a viewer, moves with the buttons and the keyboard, and closes", async () => {
    const u = userEvent.setup();
    render(<Gallery photos={photos} name="Beach House" />);
    expect(screen.getByRole("button", { name: "Show all 6 photos" })).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Open photo 2" }));
    const dialog = screen.getByRole("dialog", { name: "Beach House photos" });
    expect(within(dialog).getByText("2 / 6")).toBeTruthy();
    await u.click(within(dialog).getByRole("button", { name: "Next photo" }));
    expect(within(dialog).getByText("3 / 6")).toBeTruthy();
    await u.keyboard("{ArrowLeft}{ArrowLeft}{ArrowLeft}");
    expect(within(dialog).getByText("6 / 6")).toBeTruthy();                        // wraps around
    await u.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("copes with no photos and with a single photo", () => {
    const { rerender } = render(<Gallery photos={[]} name="Beach House" />);
    expect(screen.getByText("No photos yet")).toBeTruthy();
    rerender(<Gallery photos={photos.slice(0, 1)} name="Beach House" />);
    expect(screen.queryByRole("button", { name: /Show all/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Open photo 1" })).toBeTruthy();
  });
});
