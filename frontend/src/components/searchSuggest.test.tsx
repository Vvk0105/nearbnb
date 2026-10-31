import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { EMPTY_SEARCH, SearchBar } from "./SearchBar";
import { PlaceInput, type ListingHints } from "./PlaceInput";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const HINTS: ListingHints = {
  places: [{ text: "Bondi, NSW", suburb: "Bondi", state: "NSW", properties: 2 }],
  properties: [{ id: "p1", name: "Beach House", suburb: "Bondi", state: "NSW", type_name: "Entire place" }],
};

let calls: string[] = [];
function install({ google = false, suggest = (): Promise<Response> => json(200, HINTS) } = {}) {
  calls = [];
  vi.stubGlobal("fetch", (url: string) => {
    calls.push(url);
    if (url.endsWith("/places/status")) return json(200, { enabled: google });
    if (url.includes("/places/autocomplete")) return json(200, { enabled: true, suggestions: [{ place_id: "ChIJ_manly_beach_abc", text: "Manly NSW, Australia", main: "Manly", secondary: "NSW, Australia" }] });
    if (url.includes("/places/details/")) return json(200, { place_id: "x", label: "Manly", latitude: -33.79, longitude: 151.28, address: { address_line1: "", suburb: "Manly", state: "NSW", postcode: "2095", country: "AU" } });
    if (url.includes("/search/suggest")) return suggest();
    if (url.endsWith("/meta/booking")) return json(200, { interval_minutes: 60, step_hours: 24, max_stay_hours: 168, min_lead_hours: 2, max_advance_days: 180 });
    if (url.endsWith("/v1/me")) return json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
}
const suggestCalls = () => calls.filter((c) => c.includes("/search/suggest"));

function Harness({ onPickProperty, onSelect }: { onPickProperty?: (id: string) => void; onSelect?: (p: unknown) => void }) {
  const [text, setText] = useState("");
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <PlaceInput id="w" label="Where" value={text} onChange={setText} onPickProperty={onPickProperty} onSelect={onSelect} />
    </QueryClientProvider>
  );
}
const box = () => screen.getByLabelText("Where") as HTMLInputElement;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("suggestions under the Where box", () => {
  it("offers areas and property names from our own listings as you type, with no Google key", async () => {
    install();
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "bea");
    const list = await screen.findByRole("listbox");
    expect(within(list).getByText("Places")).toBeTruthy();
    expect(within(list).getByText("Properties")).toBeTruthy();
    expect(within(list).getByRole("option", { name: /Bondi, NSW.*2 places to stay/ })).toBeTruthy();
    expect(within(list).getByRole("option", { name: /Beach House.*Entire place · Bondi, NSW/ })).toBeTruthy();
    expect(suggestCalls().at(-1)).toContain("q=bea");
  });

  it("picking a property opens it, and leaves the text alone", async () => {
    install();
    const open = vi.fn();
    const u = userEvent.setup();
    render(<Harness onPickProperty={open} />);
    await u.type(box(), "bea");
    await u.click(await screen.findByRole("option", { name: /Beach House/ }));
    expect(open).toHaveBeenCalledWith("p1");
    expect(box().value).toBe("bea");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("picking an area fills the box, closes the list, and does not search again for what it just wrote", async () => {
    install();
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "bon");
    await u.click(await screen.findByRole("option", { name: /^Bondi, NSW/ }));
    expect(box().value).toBe("Bondi, NSW");
    const before = suggestCalls().length;
    await new Promise((r) => setTimeout(r, 400));
    expect(suggestCalls().length).toBe(before);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("works from the keyboard: arrows to move, Enter to pick, Escape to close", async () => {
    install();
    const open = vi.fn();
    const u = userEvent.setup();
    render(<Harness onPickProperty={open} />);
    await u.type(box(), "bea");
    await screen.findByRole("listbox");
    await u.keyboard("{ArrowDown}{ArrowDown}{Enter}");          // the first is the area, the second the property
    expect(open).toHaveBeenCalledWith("p1");
    await u.clear(box());
    await u.type(box(), "bea");
    await screen.findByRole("listbox");
    await u.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("needs two letters, and asks nothing when the box is not given a way to open a property", async () => {
    install();
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "b");
    await new Promise((r) => setTimeout(r, 400));
    expect(suggestCalls()).toEqual([]);
    cleanup();
    render(<Harness />);                                        // the host's address box and others: unchanged, no listing suggestions
    await u.type(box(), "beach");
    await new Promise((r) => setTimeout(r, 400));
    expect(suggestCalls()).toEqual([]);
  });

  it("stays quiet when the suggestions fail, and the box still works as plain text", async () => {
    install({ suggest: () => json(500, { error: { code: "X", message: "boom" } }) });
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "beach");
    await waitFor(() => expect(suggestCalls().length).toBeGreaterThan(0));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(screen.queryByText(/unavailable/i)).toBeNull();
    expect(box().value).toBe("beach");
  });

  it("shows nothing when nothing matches", async () => {
    install({ suggest: () => json(200, { places: [], properties: [] }) });
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "zzz");
    await waitFor(() => expect(suggestCalls().length).toBeGreaterThan(0));
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("adds Google's areas next to ours when Google is switched on, and a Google pick still returns coordinates", async () => {
    install({ google: true });
    const onSelect = vi.fn();
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} onSelect={onSelect} />);
    await u.type(box(), "man");
    await screen.findByRole("option", { name: /Manly/ });
    expect(screen.getByRole("option", { name: /^Bondi, NSW/ })).toBeTruthy();
    await u.click(screen.getByRole("option", { name: /Manly/ }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(1));
    expect(onSelect.mock.calls[0][0]).toMatchObject({ latitude: -33.79, longitude: 151.28 });
    expect(box().value).toBe("Manly NSW, Australia");
  });
});

describe("a box that already has text in it", () => {
  it("does not open the suggestions by itself (the results page shows the searched place in a fresh search bar)", async () => {
    install();
    function Prefilled() {
      const [text, setText] = useState("Glass House Mountains QLD, Australia");
      return (
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <PlaceInput id="w" label="Where" value={text} onChange={setText} onPickProperty={() => undefined} />
        </QueryClientProvider>
      );
    }
    render(<Prefilled />);
    await new Promise((r) => setTimeout(r, 500));
    expect(suggestCalls()).toEqual([]);
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("closes the list when Enter is pressed to search", async () => {
    install();
    const u = userEvent.setup();
    render(<Harness onPickProperty={() => undefined} />);
    await u.type(box(), "bea");
    await screen.findByRole("listbox");
    await u.keyboard("{Enter}");
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

describe("the search bar on the home page", () => {
  it("opens a property straight from the suggestions", async () => {
    install();
    const u = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    await u.type(await screen.findByPlaceholderText("Suburb, city or postcode"), "bea");
    await u.click(await screen.findByRole("option", { name: /Beach House/ }));
    await waitFor(() => expect(calls.some((c) => c.endsWith("/properties/p1"))).toBe(true));   // we are on that property's page (the test server has none)
  });
});

describe("the search bar fits a phone", () => {
  it("lets every box shrink to fit, so the date box cannot push out past the card", () => {
    install();
    render(<QueryClientProvider client={new QueryClient()}><SearchBar initial={EMPTY_SEARCH} onSearch={() => undefined} /></QueryClientProvider>);
    for (const id of ["sb-date", "sb-time", "sb-nights", "sb-guests", "where"]) {
      const el = document.getElementById(id)!;
      expect(el.className, id).toContain("min-w-0");
      expect(el.parentElement!.className, `${id} cell`).toContain("min-w-0");
    }
    expect(document.getElementById("sb-date")!.className).toContain("max-w-full");
  });
});
