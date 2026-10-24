import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { timeAgo } from "../lib/format";
import type { Notice } from "../lib/types";
import { NotificationBell } from "../components/NotificationBell";
import { NotificationsPage } from "./Notifications";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const minutesAgo = (m: number) => new Date(Date.now() - m * 60000).toISOString();
const notice = (id: string, over: Partial<Notice> = {}): Notice => ({
  id, kind: "booking_request_host", title: `Title ${id}`, body: "", link: "/host/requests", tone: "news", created_at: minutesAgo(5), read: false, ...over,
});

let server: Notice[] = [];
let calls: string[] = [];

function fakeServer(userBody?: unknown) {
  calls = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url.split("/v1")[1] ?? url}`);
    const unread = () => server.filter((n) => !n.read).length;
    if (url.endsWith("/v1/me")) return userBody ? json(200, userBody) : json(401, { error: { code: "NOT_AUTHENTICATED", message: "x" } });
    if (url.endsWith("/me/notifications/unread-count")) return json(200, { unread: unread() });
    if (url.endsWith("/me/notifications/read-all") && method === "POST") { server = server.map((n) => ({ ...n, read: true })); return json(200, { marked: 1, unread: 0 }); }
    const one = /\/me\/notifications\/([^/]+)\/read$/.exec(url);
    if (one && method === "POST") { server = server.map((n) => (n.id === one[1] ? { ...n, read: true } : n)); return json(200, { unread: unread() }); }
    if (url.includes("/me/notifications")) {
      const q = new URL(url, "http://x").searchParams;
      const pool = q.get("unread") === "true" ? server.filter((n) => !n.read) : server;
      const size = Number(q.get("page_size") ?? 20);
      const page = Number(q.get("page") ?? 1);
      return json(200, { total: pool.length, page, page_size: size, unread: unread(), items: pool.slice((page - 1) * size, page * size) });
    }
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
}

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

function renderBell() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/start"]}><NotificationBell /><Routes><Route path="*" element={<Where />} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => { server = []; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the bell", () => {
  it("shows how many are unread, caps the number at 9+, and has no number when all is read", async () => {
    server = Array.from({ length: 3 }, (_, i) => notice(`n${i}`));
    fakeServer();
    renderBell();
    expect(await screen.findByRole("button", { name: "Notifications, 3 unread" })).toBeTruthy();
    cleanup();
    server = Array.from({ length: 12 }, (_, i) => notice(`n${i}`));
    renderBell();
    expect((await screen.findByRole("button", { name: "Notifications, 12 unread" })).textContent).toBe("9+");
    cleanup();
    server = [notice("a", { read: true })];
    renderBell();
    const quiet = await screen.findByRole("button", { name: "Notifications" });
    await waitFor(() => expect(calls.some((c) => c.includes("unread-count"))).toBe(true));
    expect(quiet.textContent).toBe("");
  });

  it("opens to the latest messages: unread ones marked, and an action tone says so", async () => {
    const u = userEvent.setup();
    server = [
      notice("a", { title: "Pay to confirm your stay", tone: "action", body: "Pay within 30 minutes." }),
      notice("b", { title: "Payment received", read: true, created_at: minutesAgo(180) }),
    ];
    fakeServer();
    renderBell();
    await u.click(await screen.findByRole("button", { name: /Notifications/ }));
    const dialog = await screen.findByRole("dialog", { name: "Notifications" });
    expect(await within(dialog).findByText(/Pay to confirm your stay/)).toBeTruthy();
    expect(within(dialog).getByText("Unread.", { exact: false })).toBeTruthy();         // announced to screen readers
    expect(within(dialog).getByText("Pay within 30 minutes.")).toBeTruthy();
    expect(within(dialog).getByText("Needs you")).toBeTruthy();
    expect(within(dialog).getByText("3 h ago")).toBeTruthy();
    expect(within(dialog).getAllByText("Unread.", { exact: false })).toHaveLength(1);    // the read one is not announced as unread
  });

  it("opening one marks it read and goes to its page", async () => {
    const u = userEvent.setup();
    server = [notice("a", { title: "New booking request", link: "/host/requests" })];
    fakeServer();
    renderBell();
    await u.click(await screen.findByRole("button", { name: /Notifications/ }));
    await u.click(await screen.findByRole("button", { name: /New booking request/ }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/host/requests"));
    expect(calls).toContain("POST /me/notifications/a/read");
    expect(screen.queryByRole("dialog")).toBeNull();                                      // and it closes
    await waitFor(() => expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy());   // the number refreshes to nothing
  });

  it("a message that has already been read is not marked again", async () => {
    const u = userEvent.setup();
    server = [notice("a", { read: true, title: "Old news" })];
    fakeServer();
    renderBell();
    await u.click(await screen.findByRole("button", { name: /Notifications/ }));
    await u.click(await screen.findByRole("button", { name: /Old news/ }));
    expect(calls.filter((c) => c.includes("/read"))).toEqual([]);
  });

  it("mark all as read clears the number", async () => {
    const u = userEvent.setup();
    server = [notice("a"), notice("b")];
    fakeServer();
    renderBell();
    await u.click(await screen.findByRole("button", { name: "Notifications, 2 unread" }));
    await u.click(await screen.findByRole("button", { name: "Mark all as read" }));
    await waitFor(() => expect(calls).toContain("POST /me/notifications/read-all"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Mark all as read" })).toBeNull();
  });

  it("says so when there is nothing, and links to the full list", async () => {
    const u = userEvent.setup();
    fakeServer();
    renderBell();
    await u.click(await screen.findByRole("button", { name: /Notifications/ }));
    expect(await screen.findByText("You are all caught up.")).toBeTruthy();
    await u.click(screen.getByRole("link", { name: "See all notifications" }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/notifications"));
  });

  it("closes with Escape and with a click elsewhere", async () => {
    const u = userEvent.setup();
    fakeServer();
    renderBell();
    const open = async () => u.click(await screen.findByRole("button", { name: /Notifications/ }));
    await open();
    expect(screen.getByRole("dialog", { name: "Notifications" })).toBeTruthy();
    await u.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await open();
    await u.click(screen.getByTestId("where"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("the notifications page", () => {
  function renderPage() {
    return render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/notifications"]}><Routes><Route path="/notifications" element={<NotificationsPage />} /><Route path="*" element={<Where />} /></Routes></MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("lists everything, filters to unread, and marks all as read", async () => {
    const u = userEvent.setup();
    server = [notice("a", { title: "First" }), notice("b", { title: "Second", read: true })];
    fakeServer();
    renderPage();
    expect(await screen.findByText(/First/)).toBeTruthy();
    expect(screen.getByText(/Second/)).toBeTruthy();
    await u.click(screen.getByRole("tab", { name: "Unread" }));
    await waitFor(() => expect(screen.queryByText(/Second/)).toBeNull());
    expect(calls.some((c) => c.includes("unread=true"))).toBe(true);
    await u.click(screen.getByRole("button", { name: "Mark all as read (1)" }));
    await waitFor(() => expect(calls).toContain("POST /me/notifications/read-all"));
    expect(await screen.findByText("Nothing unread")).toBeTruthy();
  });

  it("is paged", async () => {
    const u = userEvent.setup();
    server = Array.from({ length: 25 }, (_, i) => notice(`n${i}`, { title: `Message ${i}`, read: true }));
    fakeServer();
    renderPage();
    expect(await screen.findByText(/Message 0/)).toBeTruthy();
    expect(screen.queryByText(/Message 24/)).toBeNull();
    await u.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText(/Message 24/)).toBeTruthy();
  });

  it("opening one goes to its page", async () => {
    const u = userEvent.setup();
    server = [notice("a", { title: "Listing approved", link: "/host/properties" })];
    fakeServer();
    renderPage();
    await u.click(await screen.findByRole("button", { name: /Listing approved/ }));
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/host/properties"));
  });

  it("has a friendly empty state", async () => {
    fakeServer();
    renderPage();
    expect(await screen.findByText("No notifications yet")).toBeTruthy();
  });
});

describe("where the bell appears", () => {
  const person = (is_admin: boolean) => ({
    id: "1", email: "p@example.com", full_name: "Pat Person", is_admin, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
    totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
  });
  const openApp = (path: string) =>
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );

  it("is in the top bar for someone logged in, with their number, and not for visitors", async () => {
    server = [notice("a"), notice("b")];
    fakeServer(person(false));
    openApp("/");
    const header = within(await screen.findByRole("banner"));
    expect(await header.findByRole("button", { name: "Notifications, 2 unread" })).toBeTruthy();
    cleanup();
    fakeServer();
    openApp("/");
    const visitorHeader = within(await screen.findByRole("banner"));
    expect(await visitorHeader.findByRole("link", { name: "Log in" })).toBeTruthy();
    expect(visitorHeader.queryByRole("button", { name: /Notifications/ })).toBeNull();
  });

  it("is also in the admin area's top bar", async () => {
    server = [notice("a")];
    fakeServer(person(true));
    openApp("/admin");
    expect(await screen.findByRole("button", { name: "Notifications, 1 unread" })).toBeTruthy();
  });

  it("the page asks for a login", async () => {
    fakeServer();
    openApp("/notifications");
    expect(await screen.findByRole("heading", { name: "Log in" })).toBeTruthy();
  });
});

describe("timeAgo", () => {
  const now = new Date("2026-10-26T12:00:00Z").getTime();
  const ago = (ms: number) => new Date(now - ms).toISOString();
  it("speaks in minutes, hours and days, then gives the date", () => {
    expect(timeAgo(ago(20_000), now)).toBe("just now");
    expect(timeAgo(ago(5 * 60_000), now)).toBe("5 min ago");
    expect(timeAgo(ago(3 * 3_600_000), now)).toBe("3 h ago");
    expect(timeAgo(ago(26 * 3_600_000), now)).toBe("1 day ago");
    expect(timeAgo(ago(5 * 86_400_000), now)).toBe("5 days ago");
    expect(timeAgo(ago(90 * 86_400_000), now)).toBe("28 Jul 2026");
    expect(timeAgo("not a date", now)).toBe("just now");
  });
});
