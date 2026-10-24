import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TeamNote } from "../components/ui";
import { UnitEditor } from "./host/PropertyPages";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const unit = (extra: object = {}) => ({
  id: "u1", property_id: "p1", name: "Entire house", description: "", price_per_24h: 25000, currency: "AUD", cleaning_fee: 2000,
  capacity: 4, quantity: 1, bedrooms: null, bathrooms: null, beds: null, max_stay_hours: null, checkin_from: "08:00", checkin_to: "22:00",
  status: "approved", review_note: null, amenities: [], rooms: [{ id: "r1", label: "1" }], photos: [], ...extra,
});
const property = (u: object) => ({
  id: "p1", name: "My Home", description: "", rules: "", type_id: null, type_name: null, address_line1: "", address_line2: "", suburb: "", state: "NSW",
  postcode: "", country: "AU", latitude: null, longitude: null, timezone: "Australia/Sydney", status: "approved", photos: [], units: [u], checklist: [], pending_revisions: [],
});

function openEditor(u: object, patch: () => Promise<Response> = () => json(200, { ...property(u), result: { applied: [], pending_review: [] } })) {
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    if (url.endsWith("/v1/host") && method === "GET") return json(200, { state: "active", id: "h1", can_create_listings: true, can_submit_listings: true, steps: [], stripe: {}, documents: [], application: {} });
    if (url.endsWith("/v1/host/properties/p1") && method === "GET") return json(200, property(u));
    if (url.endsWith("/v1/host/units/u1") && method === "PATCH") return patch();
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

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("notes from the NearBNB team", () => {
  it("are a banner only when the team asks for something", () => {
    for (const status of ["changes_requested", "rejected"]) {
      const { unmount } = render(<TeamNote status={status} note="Please add a clearer photo" />);
      expect(screen.getByRole("status").textContent).toBe("Message from the NearBNB team: Please add a clearer photo");
      unmount();
    }
  });

  it("are one quiet line once approved, so they do not look like something to act on", () => {
    render(<TeamNote status="approved" note="it's good approved" />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("Last note from the NearBNB team: “it's good approved”")).toBeTruthy();
  });

  it("show nothing when there is no note", () => {
    const { container } = render(<TeamNote status="approved" note={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("on an approved unit are quiet, and on a unit that needs changes are a banner", async () => {
    openEditor(unit({ review_note: "it's good approved" }));
    expect(await screen.findByText(/Last note from the NearBNB team/)).toBeTruthy();
    expect(screen.queryByText(/^Message from the NearBNB team/)).toBeNull();
    cleanup();
    openEditor(unit({ status: "changes_requested", review_note: "Add a photo of the kitchen" }));
    expect(await screen.findByText("Message from the NearBNB team: Add a photo of the kitchen")).toBeTruthy();
  });
});

describe("what happens when Save is pressed", () => {
  it("shows an error right above the Save button, not at the top of the form", async () => {
    const u = userEvent.setup();
    openEditor(unit(), () => json(409, { error: { code: "HAS_UPCOMING_BOOKINGS", message: "This unit has upcoming bookings, so its timing can't be changed yet." } }));
    const save = await screen.findByRole("button", { name: "Save unit" });
    await u.click(save);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("upcoming bookings");
    const firstField = screen.getByLabelText("Unit name");
    expect(firstField.compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();     // after the first field, not before it
    expect(alert.compareDocumentPosition(save) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();           // and just before the button
  });

  it("shows the success message in the same place, and brings it into view", async () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    const u = userEvent.setup();
    openEditor(unit());
    await u.click(await screen.findByRole("button", { name: "Save unit" }));
    expect(await screen.findByText("Saved.")).toBeTruthy();
    await waitFor(() => expect(scrolled).toHaveBeenCalled());
  });

  it("shows nothing until there is something to say", async () => {
    openEditor(unit());
    await screen.findByRole("button", { name: "Save unit" });
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Saved.")).toBeNull();
  });
});
