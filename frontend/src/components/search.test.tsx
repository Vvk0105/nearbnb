import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { apiQueryFrom } from "../pages/Search";
import { PlaceInput } from "./PlaceInput";
import { SearchBar, searchToParams, readSearch, EMPTY_SEARCH, type SearchValues } from "./SearchBar";
import type { Place } from "../lib/types";
import { useState } from "react";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

describe("search URL <-> API conversion", () => {
  it("turns the page URL into the API query", () => {
    const url = new URLSearchParams("q=Bondi&lat=-33.89&lng=151.27&date=2099-06-15&time=14:00&nights=2&guests=3&min=100&max=250.50&types=a,b&amenities=x&sort=price_asc&page=2");
    const api = new URLSearchParams(apiQueryFrom(url));
    expect(api.get("q")).toBe("Bondi");
    expect(api.get("lat")).toBe("-33.89");
    expect(api.get("checkin")).toBe("2099-06-15T14:00");
    expect(api.get("hours")).toBe("48");                 // 2 nights = 48 hours
    expect(api.get("guests")).toBe("3");
    expect(api.get("min_price")).toBe("10000");          // dollars in the URL, cents for the API
    expect(api.get("max_price")).toBe("25050");
    expect(api.getAll("type_ids")).toEqual(["a", "b"]);
    expect(api.getAll("amenity_ids")).toEqual(["x"]);
    expect(api.get("sort")).toBe("price_asc");
    expect(api.get("page")).toBe("2");
  });

  it("sends no date filter when no date was chosen", () => {
    const api = new URLSearchParams(apiQueryFrom(new URLSearchParams("q=Bondi")));
    expect(api.has("checkin")).toBe(false);
    expect(api.has("hours")).toBe(false);
    expect(api.get("sort")).toBe("relevance");
  });

  it("round-trips search values through the URL", () => {
    const v: SearchValues = { q: "Bondi", lat: "-33.8", lng: "151.2", date: "2099-06-15", time: "16:00", nights: "3", guests: "4" };
    expect(readSearch(searchToParams(v))).toEqual(v);
    expect(searchToParams({ ...EMPTY_SEARCH, q: "  " }).toString()).toBe("guests=2");
  });

  it("only includes coordinates when both are present", () => {
    expect(searchToParams({ ...EMPTY_SEARCH, lat: "1" }).has("lat")).toBe(false);
  });
});

describe("PlaceInput", () => {
  let calls: string[] = [];
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const install = (enabled: boolean) => {
    calls = [];
    vi.stubGlobal("fetch", (url: string) => {
      calls.push(url);
      if (url.endsWith("/places/status")) return json(200, { enabled });
      if (url.includes("/places/autocomplete")) return json(200, { enabled, suggestions: [{ place_id: "ChIJ_bondi_beach_abc", text: "Bondi Beach NSW, Australia", main: "Bondi Beach", secondary: "NSW, Australia" }] });
      if (url.includes("/places/details/")) return json(200, { place_id: "x", label: "Bondi Beach", latitude: -33.89, longitude: 151.27, address: { address_line1: "", suburb: "Bondi Beach", state: "NSW", postcode: "2026", country: "AU" } });
      return json(404, {});
    });
  };

  function Harness({ onSelect }: { onSelect: (p: Place) => void }) {
    const [text, setText] = useState("");
    return <QueryClientProvider client={new QueryClient()}><PlaceInput id="p" label="Where" value={text} onChange={setText} onSelect={onSelect} /></QueryClientProvider>;
  }

  it("behaves like a plain text box when Google is not configured", async () => {
    install(false);
    render(<Harness onSelect={() => undefined} />);
    await userEvent.setup().type(screen.getByLabelText("Where"), "Bondi Beach");
    await new Promise((r) => setTimeout(r, 400));
    expect((screen.getByLabelText("Where") as HTMLInputElement).value).toBe("Bondi Beach");
    expect(calls.some((c) => c.includes("/autocomplete"))).toBe(false);
  });

  it("suggests places, then returns coordinates when one is picked", async () => {
    install(true);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Where"), "bondi");
    await user.click(await screen.findByRole("option", { name: /Bondi Beach/ }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
    expect(onSelect.mock.calls[0][0]).toMatchObject({ latitude: -33.89, longitude: 151.27, address: { suburb: "Bondi Beach", state: "NSW" } });
    expect((screen.getByLabelText("Where") as HTMLInputElement).value).toBe("Bondi Beach NSW, Australia");
    // the autocomplete request and the details request share one session token (Google bills them together)
    const token = (u: string) => new URL(u, "http://x").searchParams.get("token");
    const auto = calls.find((c) => c.includes("/autocomplete"))!, details = calls.find((c) => c.includes("/details/"))!;
    expect(token(auto)).toBeTruthy();
    expect(token(auto)).toBe(token(details));
  });

  it("does not search for fewer than three characters", async () => {
    install(true);
    render(<Harness onSelect={() => undefined} />);
    await userEvent.setup().type(screen.getByLabelText("Where"), "bo");
    await new Promise((r) => setTimeout(r, 400));
    expect(calls.some((c) => c.includes("/autocomplete"))).toBe(false);
  });
});

describe("SearchBar", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", (url: string) => {
      if (url.endsWith("/meta/booking")) return json(200, { interval_minutes: 60, step_hours: 24, max_stay_hours: 168, min_lead_hours: 2, max_advance_days: 180 });
      if (url.endsWith("/places/status")) return json(200, { enabled: false });
      return json(404, {});
    });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("submits exactly what was entered", async () => {
    const onSearch = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><SearchBar initial={EMPTY_SEARCH} onSearch={onSearch} /></QueryClientProvider>);
    const user = userEvent.setup();
    await user.type(screen.getByPlaceholderText("Suburb, city or postcode"), "Bondi");
    await user.type(screen.getByLabelText("Check-in"), "2099-06-15");
    await user.selectOptions(await screen.findByLabelText("Nights"), "3");
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(onSearch).toHaveBeenCalledWith({ q: "Bondi", lat: "", lng: "", date: "2099-06-15", time: "14:00", nights: "3", guests: "2" });
  });

  it("offers 24 hourly check-in times and up to 7 nights", async () => {
    render(<QueryClientProvider client={new QueryClient()}><SearchBar initial={EMPTY_SEARCH} onSearch={() => undefined} /></QueryClientProvider>);
    expect((await screen.findByLabelText("Time") as HTMLSelectElement).options).toHaveLength(24);
    await waitFor(() => expect((screen.getByLabelText("Nights") as HTMLSelectElement).options).toHaveLength(7));
  });
});
