import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { IcalConflictRow, IcalSourceRow, IcalUnitState } from "../lib/types";
import { CalendarSync } from "../components/CalendarSync";
import { AdminCalendarPage } from "./admin/AdminCalendarPage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

type Call = { url: string; method?: string; body?: Record<string, unknown> };
let calls: Call[] = [];
const sent = (suffix: string) => calls.filter((c) => c.url.endsWith(suffix) && c.method && c.method !== "GET").at(-1)?.body;
const hit = (suffix: string, method = "POST") => calls.some((c) => c.url.endsWith(suffix) && c.method === method);
const changes = (init?: RequestInit) => Boolean(init?.method && init.method !== "GET");   // the app sends GET explicitly
const stub = (handler: (url: string, init?: RequestInit) => Promise<Response>) =>
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    return handler(url, init);
  });
beforeEach(() => { calls = []; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const source = (over: Partial<IcalSourceRow> = {}): IcalSourceRow => ({
  id: "s1", unit_id: "u1", name: "Airbnb", url: "https://airbnb.example/1.ics", checkin_time: "15:00", checkout_time: "11:00", active: true,
  last_synced_at: new Date(Date.now() - 5 * 60_000).toISOString(), last_attempt_at: null, last_error: null, events_count: 2, failing: false, ...over,
});
const conflict: IcalConflictRow = {
  id: "c1", unit_id: "u1", source: "Airbnb", status: "open", reference: "NB-ABCD2345", booking_id: "b1", booking_status: "confirmed", property: "Beach House",
  booking_from: "21 Nov 2:00 PM", booking_to: "22 Nov 2:00 PM", clash_from: "20 Nov 3:00 PM", clash_to: "24 Nov 11:00 AM", detected_at: "2099-01-01T00:00:00Z", resolved_at: null, note: null,
};
const state = (over: Partial<IcalUnitState> = {}): IcalUnitState => ({ eligible: true, reason: null, sources: [source()], export_path: "/ical/SECRETTOKEN123456789012.ics", max_sources: 3, conflicts: [], ...over });

describe("calendar sync for a unit", () => {
  it("explains when a unit with several rooms cannot use it", async () => {
    stub(() => json(200, state({ eligible: false, reason: "Calendar sync works for units with a single room. This unit has several.", sources: [], export_path: null })));
    wrap(<CalendarSync unitId="u1" />);
    expect(await screen.findByText(/single room/)).toBeTruthy();
    expect(screen.queryByLabelText("Your private calendar link")).toBeNull();
  });

  it("shows our private link to paste into the other site, and copies it", async () => {
    stub(() => json(200, state()));
    const u = userEvent.setup();                                                                   // installs its own clipboard, so spy on that one
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    wrap(<CalendarSync unitId="u1" />);
    const box = (await screen.findByLabelText("Your private calendar link")) as HTMLInputElement;
    expect(box.value).toMatch(/\/ical\/SECRETTOKEN123456789012\.ics$/);
    expect(box.readOnly).toBe(true);
    expect(screen.getByText(/never contains guest details/)).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Copy" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(box.value));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeTruthy();
  });

  it("replaces the link only after the host confirms", async () => {
    stub(() => json(200, state()));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    wrap(<CalendarSync unitId="u1" />);
    const u = userEvent.setup();
    const replace = await screen.findByRole("button", { name: /Replace this link/ });
    await u.click(replace);
    expect(hit("/ical/export/rotate")).toBe(false);
    await u.click(replace);
    await waitFor(() => expect(hit("/ical/export/rotate")).toBe(true));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/New link created/)).toBeTruthy();
  });

  it("lists each calendar with when it was read and what it blocks", async () => {
    stub(() => json(200, state({ sources: [source(), source({ id: "s2", name: "Booking.com", events_count: 1, active: false })] })));
    wrap(<CalendarSync unitId="u1" />);
    expect(await screen.findByText("Airbnb")).toBeTruthy();
    expect(screen.getByText(/Last read 5 min ago · 2 booked periods blocked here/)).toBeTruthy();
    expect(screen.getByText(/1 booked period blocked here/)).toBeTruthy();
    expect(screen.getByText("(paused)")).toBeTruthy();
    expect(screen.getAllByText(/Whole days are blocked from 15:00 to 11:00/)).toHaveLength(2);
  });

  it("shows a problem in plain words and reassures that blocked dates stay blocked", async () => {
    stub(() => json(200, state({ sources: [source({ failing: true, last_error: "The other site took too long to answer." })] })));
    wrap(<CalendarSync unitId="u1" />);
    expect(await screen.findByText(/Problem: The other site took too long to answer\. The dates already blocked stay blocked\./)).toBeTruthy();
  });

  it("syncs now, pauses and disconnects (after confirming)", async () => {
    stub((url, init) => (changes(init) ? json(200, {}) : json(200, state())));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    wrap(<CalendarSync unitId="u1" />);
    const u = userEvent.setup();
    await u.click(await screen.findByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(hit("/ical/sources/s1/sync")).toBe(true));
    expect(await screen.findByText("Airbnb synced.")).toBeTruthy();
    await u.click(screen.getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(sent("/ical/sources/s1")).toEqual({ active: false }));
    await u.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(hit("/ical/sources/s1", "DELETE")).toBe(true));
  });

  it("shows the server's reason when syncing is refused", async () => {
    stub((url, init) => (changes(init) ? json(429, { error: { code: "SYNC_TOO_SOON", message: "Please wait a minute before syncing again." } }) : json(200, state())));
    wrap(<CalendarSync unitId="u1" />);
    await userEvent.setup().click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Please wait a minute before syncing again.")).toBeTruthy();
  });

  it("connects a new calendar with the chosen times and reports what it found", async () => {
    stub((url, init) => (changes(init) ? json(201, { source: source(), sync: { ok: true, blocks: 3, conflicts_opened: 0 } }) : json(200, state({ sources: [] }))));
    wrap(<CalendarSync unitId="u1" />);
    const u = userEvent.setup();
    const submit = (await screen.findByRole("button", { name: "Connect calendar" })) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    await u.type(screen.getByLabelText("Name"), "Airbnb");
    await u.type(screen.getByLabelText(/Calendar link/), "https://www.airbnb.com.au/calendar/ical/1.ics?s=x");
    await u.clear(screen.getByLabelText(/Check-in time on that site/));
    await u.type(screen.getByLabelText(/Check-in time on that site/), "14:00");
    await u.click(submit);
    await waitFor(() => expect(sent("/host/units/u1/ical/sources")).toEqual({ name: "Airbnb", url: "https://www.airbnb.com.au/calendar/ical/1.ics?s=x", checkin_time: "14:00", checkout_time: "11:00" }));
    expect(await screen.findByText(/Connected\. 3 booked periods found\./)).toBeTruthy();
  });

  it("shows why a link was refused and when the first read failed", async () => {
    stub((url, init) => (changes(init) ? json(422, { error: { code: "INVALID_CALENDAR_LINK", message: "That address is not a public website, so it cannot be used." } }) : json(200, state({ sources: [] }))));
    wrap(<CalendarSync unitId="u1" />);
    const u = userEvent.setup();
    await u.type(await screen.findByLabelText("Name"), "x");
    await u.type(screen.getByLabelText(/Calendar link/), "http://localhost/a.ics");
    await u.click(screen.getByRole("button", { name: "Connect calendar" }));
    expect(await screen.findByText("That address is not a public website, so it cannot be used.")).toBeTruthy();
    cleanup();
    stub((url, init) => (changes(init) ? json(201, { source: source(), sync: { ok: false, error: "Could not connect to the other site." } }) : json(200, state({ sources: [] }))));
    wrap(<CalendarSync unitId="u1" />);
    await u.type(await screen.findByLabelText("Name"), "x");
    await u.type(screen.getByLabelText(/Calendar link/), "https://example.com/a.ics");
    await u.click(screen.getByRole("button", { name: "Connect calendar" }));
    expect(await screen.findByText(/Added, but the first read failed: Could not connect to the other site\./)).toBeTruthy();
  });

  it("hides the form once the limit is reached", async () => {
    stub(() => json(200, state({ sources: [source(), source({ id: "s2" }), source({ id: "s3" })] })));
    wrap(<CalendarSync unitId="u1" />);
    expect(await screen.findByText(/up to 3 calendars/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect calendar" })).toBeNull();
  });

  it("shows clashes with our own bookings, says nothing was cancelled, and lets the host mark them dealt with", async () => {
    stub((url, init) => (changes(init) ? json(200, { ok: true }) : json(200, state({ conflicts: [conflict] }))));
    wrap(<CalendarSync unitId="u1" />);
    const list = await screen.findByLabelText("Calendar conflicts");
    expect(within(list).getByText(/Airbnb shows 20 Nov 3:00 PM to 24 Nov 11:00 AM as taken/)).toBeTruthy();
    expect(within(list).getByText(/Nothing was cancelled/)).toBeTruthy();
    expect(within(list).getByText("NB-ABCD2345")).toBeTruthy();
    await userEvent.setup().click(within(list).getByRole("button", { name: "I have dealt with this" }));
    await waitFor(() => expect(hit("/host/ical/conflicts/c1/dismiss")).toBe(true));
    expect(await screen.findByText("Marked as dealt with.")).toBeTruthy();
  });
});

describe("admin calendar page", () => {
  const failing = { ...source({ failing: true, last_error: "Could not connect to the other site." }), property: "Beach House", unit: "Entire house", failures: 6 };

  it("lists open conflicts and calendars that stopped syncing, and can try one again", async () => {
    stub((url, init) => (changes(init) ? json(200, {}) : json(200, { conflicts: { total: 1, page: 1, page_size: 25, items: [conflict] }, failing_sources: [failing] })));
    wrap(<AdminCalendarPage />);
    expect(await screen.findByText(/Airbnb shows 20 Nov 3:00 PM/)).toBeTruthy();
    expect(screen.getByText(/Beach House · Entire house · Airbnb/)).toBeTruthy();
    expect(screen.getByText(/failed 6 times/)).toBeTruthy();
    expect(screen.getByText("Could not connect to the other site.")).toBeTruthy();
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Try again now" }));
    await waitFor(() => expect(hit("/admin/ical/sources/s1/sync")).toBe(true));
    await u.click(screen.getByRole("button", { name: "I have dealt with this" }));
    await waitFor(() => expect(hit("/admin/ical/conflicts/c1/dismiss")).toBe(true));
  });

  it("says when everything is fine", async () => {
    stub(() => json(200, { conflicts: { total: 0, page: 1, page_size: 25, items: [] }, failing_sources: [] }));
    wrap(<AdminCalendarPage />);
    expect(await screen.findByText("No open conflicts.")).toBeTruthy();
    expect(screen.getByText("Every connected calendar is syncing.")).toBeTruthy();
  });
});
