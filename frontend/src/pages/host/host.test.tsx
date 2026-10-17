import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApplyPage } from "./HostPages";
import { UnitEditor } from "./PropertyPages";

type Call = { method: string; url: string; body: unknown };
let calls: Call[] = [];

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const hostDraft = {
  state: "draft", id: "h1", review_status: "draft", review_note: null, editable: true, application: { legal_name: "Hannah Host", country: "AU" },
  documents: [{ id: "d1", doc_type: "government_id", original_name: "id.png", size_bytes: 2048, content_type: "image/png", created_at: null }],
  steps: [], stripe: { connected: false, details_submitted: false, payouts_enabled: false, transfers_active: false, ready: false, requirements: [], dev_mode: true },
  can_create_listings: false, can_submit_listings: false,
};

const unit = {
  id: "u1", property_id: "p1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 2000,
  capacity: 4, quantity: 1, bedrooms: null, bathrooms: null, beds: null, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00",
  status: "draft", review_note: null, amenities: [{ id: "a1", name: "Wi-Fi" }], rooms: [{ id: "r1", label: "1" }], photos: [],
};
const property = {
  id: "p1", name: "Beach House", description: "", rules: "", type_id: null, type_name: null, address_line1: "", address_line2: "", suburb: "", state: "NSW",
  postcode: "", country: "AU", latitude: null, longitude: null, timezone: "Australia/Sydney", status: "draft", photos: [], units: [unit],
  checklist: [], pending_revisions: [],
};

function installFakeApi() {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : init?.body;
    calls.push({ method, url, body });
    if (url.endsWith("/v1/host") && method === "GET") return json(200, hostDraft);
    if (url.endsWith("/v1/host/application") && method === "PATCH") return json(200, hostDraft);
    if (url.endsWith("/v1/host/application/submit")) return json(200, { ...hostDraft, state: "pending_review", editable: false });
    if (url.endsWith("/v1/host/properties/p1") && method === "GET") return json(200, property);
    if (url.endsWith("/v1/host/units/u1") && method === "PATCH") return json(200, { ...property, result: { applied: ["price_per_24h"], pending_review: [] } });
    if (url.includes("/v1/host/units/u1/calendar")) return json(200, { timezone: "Australia/Sydney", rooms: [{ id: "r1", label: "1" }], entries: [] });
    if (url.endsWith("/v1/meta/listing")) return json(200, { property_types: [], amenities: [{ id: "a1", name: "Wi-Fi" }, { id: "a2", name: "Pool" }] });
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
}

function renderAt(path: string, route: string, element: React.ReactElement) {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={route} element={element} />
          <Route path="/host" element={<p>host home</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("host screens send the right data", () => {
  beforeEach(installFakeApi);
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("unit form converts dollars to cents and sends amenities", async () => {
    renderAt("/host/properties/p1/units/u1", "/host/properties/:propertyId/units/:unitId", <UnitEditor />);
    const price = await screen.findByLabelText("Price per 24 hours ($)");
    expect((price as HTMLInputElement).value).toBe("250.00"); // 25000 cents shown as dollars
    const user = userEvent.setup();
    await user.clear(price);
    await user.type(price, "312.50");
    await user.click(await screen.findByLabelText("Pool"));
    await user.click(screen.getByRole("button", { name: "Save unit" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.endsWith("/host/units/u1"))).toBe(true));
    const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/host/units/u1"))!.body as Record<string, unknown>;
    expect(patch.price_per_24h).toBe(31250);
    expect(patch.cleaning_fee).toBe(2000);
    expect(patch.capacity).toBe(4);
    expect(patch.amenity_ids).toEqual(["a1", "a2"]);
    expect(patch).not.toHaveProperty("bedrooms"); // empty optional numbers are not sent as 0
  });

  it("application can only be submitted after accepting the Host Terms, and saves first", async () => {
    renderAt("/host/apply", "/host/apply", <ApplyPage />);
    const submit = await screen.findByRole("button", { name: "Submit application" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    const user = userEvent.setup();
    await user.click(screen.getByRole("checkbox"));
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    await user.click(submit);

    await waitFor(() => expect(screen.getByText("host home")).toBeTruthy()); // navigated after success
    const order = calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.url.split("/v1")[1]}`);
    expect(order).toEqual(["PATCH /host/application", "POST /host/application/submit"]);
    expect(calls.find((c) => c.url.endsWith("/submit"))!.body).toEqual({ accept_host_terms: true });
  });
});
