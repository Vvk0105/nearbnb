import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { AdminIssueRow, AdminReviewRow, BookingItem, ReviewInfo, SearchItem } from "../lib/types";
import { PropertyCard } from "../components/PropertyCard";
import { PropertyReviews, RatingSummary, ReportProblem, ReviewBox, Stars } from "../components/Reviews";
import { AdminIssuesPage, AdminReviewsPage } from "./admin/AdminReviewsIssuesPages";
import { RequestsPage } from "./host/RequestsPage";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

function wrap(ui: React.ReactNode, path = "/") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[path]}><Routes><Route path="*" element={ui} /></Routes></MemoryRouter>
    </QueryClientProvider>,
  );
}

type Call = { url: string; method?: string; body?: Record<string, unknown> };
let calls: Call[] = [];
const stub = (handler: (url: string, init?: RequestInit) => Promise<Response>) =>
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    return handler(url, init);
  });
const sent = (suffix: string) => calls.filter((c) => c.url.endsWith(suffix) && c.method && c.method !== "GET").at(-1)?.body;
beforeEach(() => { calls = []; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const base = {
  id: "b1", reference: "NB-ABCD2345", status: "completed", currency: "AUD", can_report_issue: true, issues: [],
  property: { id: "p1", name: "Beach House", suburb: "", state: "", cover_url: null }, unit: { id: "u1", name: "Entire house" }, price: {},
} as unknown as BookingItem;
const info = (over: Partial<ReviewInfo> = {}): ReviewInfo => ({ closes_at: "2099-06-30T00:00:00Z", open: true, can_review: true, mine: null, theirs: null, ...over });
const withReview = (over: Partial<ReviewInfo>) => ({ ...base, review: info(over) }) as BookingItem;

describe("stars and summaries", () => {
  it("shows the number as well as the stars", () => {
    wrap(<><Stars value={4.5} /><RatingSummary average={4.5} count={12} /><RatingSummary average={null} count={0} /></>);
    expect(screen.getAllByLabelText("4.5 out of 5")).toHaveLength(2);
    expect(screen.getByText("(12 reviews)")).toBeTruthy();
    expect(screen.getByText("No reviews yet")).toBeTruthy();
  });
  it("uses the singular for one review", () => {
    wrap(<RatingSummary average={5} count={1} />);
    expect(screen.getByText("(1 review)")).toBeTruthy();
  });
});

describe("guest review box", () => {
  beforeEach(() => stub(() => json(201, {})));

  it("explains that reviews are public and shown together, and needs a rating before it can be sent", async () => {
    wrap(<ReviewBox booking={withReview({})} as="guest" />);
    expect(screen.getByText(/Your review will be public\. It is shown together with the host's review of you/)).toBeTruthy();
    const submit = screen.getByRole("button", { name: "Submit review" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    const u = userEvent.setup();
    await u.click(screen.getByRole("radio", { name: "4 stars" }));
    expect(screen.getByRole("radio", { name: "4 stars" }).getAttribute("aria-checked")).toBe("true");
    await u.type(screen.getByLabelText(/Comment/), "Clean and quiet.");
    expect(submit.disabled).toBe(false);
    await u.click(submit);
    await waitFor(() => expect(sent("/me/bookings/b1/review")).toEqual({ rating: 4, comment: "Clean and quiet." }));
  });

  it("shows the server's reason when sending fails", async () => {
    stub(() => json(409, { error: { code: "WINDOW_CLOSED", message: "The time to review this stay has passed." } }));
    wrap(<ReviewBox booking={withReview({})} as="guest" />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("radio", { name: "5 stars" }));
    await u.click(screen.getByRole("button", { name: "Submit review" }));
    expect(await screen.findByText("The time to review this stay has passed.")).toBeTruthy();
  });

  it("after submitting, says it is saved and when it will appear", () => {
    wrap(<ReviewBox booking={withReview({ can_review: false, mine: { rating: 4, comment: "Lovely", status: "submitted", submitted_at: "2099-06-20T00:00:00Z" } })} as="guest" />);
    expect(screen.getByText("Lovely")).toBeTruthy();
    expect(screen.getByText(/appears once the host has reviewed too, or on 2099-06-30/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Submit review" })).toBeNull();
  });

  it("once published says it is public, and never shows anything from the host", () => {
    wrap(<ReviewBox booking={withReview({ can_review: false, mine: { rating: 5, comment: "Great", status: "published", submitted_at: "x" }, theirs: null })} as="guest" />);
    expect(screen.getByText(/now public on the property page/)).toBeTruthy();
    expect(screen.queryByText(/The guest's review of your property/)).toBeNull();
  });

  it("says when the review period is over, or the review was hidden", () => {
    const { unmount } = wrap(<ReviewBox booking={withReview({ can_review: false, open: false })} as="guest" />);
    expect(screen.getByText(/review period for this stay ended on 2099-06-30/)).toBeTruthy();
    unmount();
    wrap(<ReviewBox booking={withReview({ can_review: false, mine: { rating: 1, comment: "", status: "hidden", submitted_at: "x" } })} as="guest" />);
    expect(screen.getByText(/hidden by NearBnB/)).toBeTruthy();
  });

  it("shows nothing for a stay that is not over", () => {
    wrap(<ReviewBox booking={{ ...base, review: null } as BookingItem} as="guest" />);
    expect(screen.queryByText("Review your stay")).toBeNull();
  });
});

describe("host review box", () => {
  it("is private, posts to the host endpoint, and shows the guest's published review of the property", async () => {
    stub(() => json(201, {}));
    const { unmount } = wrap(<ReviewBox booking={withReview({})} as="host" />);
    expect(screen.getByText(/Your review of the guest is private: only other hosts who get a request from them/)).toBeTruthy();
    const u = userEvent.setup();
    await u.click(screen.getByRole("radio", { name: "2 stars" }));
    await u.click(screen.getByRole("button", { name: "Submit review" }));
    await waitFor(() => expect(sent("/host/bookings/b1/review")).toEqual({ rating: 2, comment: "" }));
    unmount();
    wrap(<ReviewBox booking={withReview({ can_review: false, mine: { rating: 4, comment: "Tidy", status: "published", submitted_at: "x" }, theirs: { rating: 5, comment: "Fantastic host" } })} as="host" />);
    expect(screen.getByText(/Only other hosts who get a request from this guest, and NearBnB, can see it/)).toBeTruthy();
    expect(screen.getByText("Fantastic host")).toBeTruthy();
  });
});

describe("reporting a problem", () => {
  const categories = { guest: ["property_condition", "safety"], host: ["guest_behaviour", "damage"] };
  beforeEach(() => stub((url, init) => (init?.method === "POST" ? json(201, {}) : json(200, categories))));

  it("offers the categories for the person's role and sends the report only when it is complete", async () => {
    wrap(<ReportProblem booking={base} as="host" />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Report a problem" }));
    const send = (await screen.findByRole("button", { name: "Send report" })) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    const select = await screen.findByLabelText("What kind of problem?");
    await waitFor(() => expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choose...", "guest behaviour", "damage"]));
    await u.selectOptions(select, "damage");
    await u.type(screen.getByLabelText("What happened?"), "short");
    expect(send.disabled).toBe(true);
    await u.type(screen.getByLabelText("What happened?"), " but now it is long enough");
    expect(send.disabled).toBe(false);
    await u.click(send);
    await waitFor(() => expect(sent("/host/bookings/b1/issues")).toEqual({ category: "damage", body: "short but now it is long enough" }));
  });

  it("lists earlier reports with the outcome, and nothing internal", () => {
    const b = { ...base, can_report_issue: false, issues: [{ id: "i1", category: "safety", body: "The railing was loose and unsafe.", status: "resolved", resolution: "We have had it repaired.", created_at: "2099-01-01T00:00:00Z" }] } as BookingItem;
    wrap(<ReportProblem booking={b} as="guest" />);
    expect(screen.getByText("The railing was loose and unsafe.")).toBeTruthy();
    expect(screen.getByText(/We have had it repaired\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Report a problem" })).toBeNull();
  });

  it("is not shown when nothing can be reported and nothing was", () => {
    wrap(<ReportProblem booking={{ ...base, can_report_issue: false } as BookingItem} as="guest" />);
    expect(screen.queryByText("Problems")).toBeNull();
  });

  it("shows the server's reason when the report is refused", async () => {
    stub((url, init) => (init?.method === "POST" ? json(409, { error: { code: "INVALID_STATE", message: "You can report a problem once a booking is confirmed." } }) : json(200, categories)));
    wrap(<ReportProblem booking={base} as="guest" />);
    const u = userEvent.setup();
    await u.click(screen.getByRole("button", { name: "Report a problem" }));
    await u.selectOptions(await screen.findByLabelText("What kind of problem?"), "safety");
    await u.type(screen.getByLabelText("What happened?"), "Something went wrong during the stay.");
    await u.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByText("You can report a problem once a booking is confirmed.")).toBeTruthy();
  });
});

describe("public reviews and ratings", () => {
  const page = (n: number) => ({ total: 3, page: n, page_size: 2, average: 4.5, count: 3,
    items: n === 1 ? [{ id: "r1", rating: 5, comment: "Spotless.", author: "Gina", month: "June 2099" }, { id: "r2", rating: 4, comment: "Quiet street.", author: "Sam", month: "May 2099" }]
      : [{ id: "r3", rating: 4, comment: "Great host.", author: "Lee", month: "April 2099" }] });

  it("shows the rating, each review with first name and month, and loads more on request", async () => {
    stub((url) => json(200, page(Number(new URL(url, "http://x").searchParams.get("page")))));
    wrap(<PropertyReviews propertyId="p1" />);
    expect(await screen.findByText("Spotless.")).toBeTruthy();
    expect(screen.getByText("(3 reviews)")).toBeTruthy();
    expect(screen.getByText("Gina")).toBeTruthy();
    expect(screen.getByText(/June 2099/)).toBeTruthy();
    expect(screen.queryByText("Great host.")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Show more reviews" }));
    expect(await screen.findByText("Great host.")).toBeTruthy();
    expect(screen.getByText("Spotless.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Show more reviews" })).toBeNull();
  });

  it("shows nothing when a property has no reviews", async () => {
    stub(() => json(200, { total: 0, page: 1, page_size: 10, average: null, count: 0, items: [] }));
    wrap(<PropertyReviews propertyId="p1" />);
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByText("Guest reviews")).toBeNull();
  });

  it("search cards show the rating when there is one", () => {
    const item = { id: "p1", name: "Beach House", type_name: null, suburb: "Bondi", state: "NSW", cover_url: null, thumb_url: null, currency: "AUD", min_price: 25000, distance_km: null,
      units: [{ id: "u", name: "x", price_per_24h: 25000, capacity: 2 }], rating: { average: 4.5, count: 12 } } as SearchItem;
    wrap(<PropertyCard item={item} carry="" />);
    expect(screen.getByText("(12 reviews)")).toBeTruthy();
    cleanup();
    wrap(<PropertyCard item={{ ...item, rating: null }} carry="" />);
    expect(screen.queryByText(/reviews?\)/)).toBeNull();
  });
});

describe("what a host sees about a guest", () => {
  it("shows other hosts' ratings and comments, and the number of stays, on the request", async () => {
    const card = {
      id: "b9", reference: "NB-X", status: "requested", hours: 24, guests: 2, currency: "AUD", timezone: "Australia/Sydney", check_in_label: "15 Jun 2:00 PM", check_out_label: "16 Jun 2:00 PM",
      expires_at: "2099-01-01T00:30:00Z", seconds_to_respond: 1500, property: { id: "p", name: "Beach House", suburb: "", state: "", cover_url: null }, unit: { id: "u", name: "Entire house" }, room: null,
      price: { host_payable: 22000, commission_percent: 12 }, hours_: 24,
      guest: { full_name: "Gina Guest", age: 34, member_since: null, email_verified: true, social_links: [], reviews: { count: 2, average: 3.5, completed_stays: 4, items: [{ rating: 2, comment: "Noisy and left a mess.", month: "May 2099" }, { rating: 5, comment: "", month: "April 2099" }] } },
    } as unknown as BookingItem;
    stub(() => json(200, { items: [card], total: 1, page: 1, page_size: 25, counts: { pending: 1, awaiting_payment: 0, upcoming: 0, past: 0 } }));
    wrap(<RequestsPage />, "/host/requests");
    const record = await screen.findByLabelText("Guest track record");
    expect(within(record).getByText(/from 2 hosts/)).toBeTruthy();
    expect(within(record).getByText("4 stays completed on NearBnB")).toBeTruthy();
    expect(within(record).getByText(/"Noisy and left a mess\."/)).toBeTruthy();
    expect(within(record).getByText(/"No comment"/)).toBeTruthy();
  });

  it("says so when a guest has no history", async () => {
    const card = { id: "b9", reference: "NB-X", status: "requested", currency: "AUD", property: { id: "p", name: "Beach House", suburb: "", state: "", cover_url: null }, unit: { id: "u", name: "x" }, price: { host_payable: 1 },
      guest: { full_name: "New Guest", age: null, member_since: null, email_verified: true, social_links: [], reviews: { count: 0, average: null, completed_stays: 0, items: [] } } } as unknown as BookingItem;
    stub(() => json(200, { items: [card], total: 1, page: 1, page_size: 25, counts: { pending: 1, awaiting_payment: 0, upcoming: 0, past: 0 } }));
    wrap(<RequestsPage />, "/host/requests");
    const record = await screen.findByLabelText("Guest track record");
    expect(within(record).getByText("No reviews from other hosts yet.")).toBeTruthy();
    expect(within(record).getByText("0 stays completed on NearBnB")).toBeTruthy();
  });
});

describe("admin moderation", () => {
  const review: AdminReviewRow = { id: "rv1", reference: "NB-ABCD2345", booking_id: "b1", property: "Beach House", direction: "guest_to_host", rating: 1, comment: "Terrible, avoid.", status: "published",
    author: "Gina Guest", author_email: "gina@example.com", subject: "Hannah", submitted_at: "2099-01-01T00:00:00Z", published_at: "2099-01-02T00:00:00Z", hidden_reason: null };

  it("hides a review only with a reason, then offers to restore it", async () => {
    let current = review;
    stub((url, init) => {
      if (init?.method === "POST") { current = { ...review, status: url.endsWith("/hide") ? "hidden" : "published", hidden_reason: url.endsWith("/hide") ? "Abusive" : null }; return json(200, {}); }
      return json(200, { total: 1, page: 1, page_size: 25, items: [current] });
    });
    wrap(<AdminReviewsPage />);
    const u = userEvent.setup();
    expect(await screen.findByText("Terrible, avoid.")).toBeTruthy();
    expect(screen.getByText(/guest about the stay \(public\)/)).toBeTruthy();
    const hide = screen.getByRole("button", { name: "Hide review" }) as HTMLButtonElement;
    expect(hide.disabled).toBe(true);
    await u.type(screen.getByLabelText(/Reason to hide/), "Abusive");
    await u.click(hide);
    await waitFor(() => expect(sent("/rv1/hide")).toEqual({ reason: "Abusive" }));
    await u.click(await screen.findByRole("button", { name: "Restore" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/rv1/restore"))).toBe(true));
  });

  it("filters on the server", async () => {
    stub(() => json(200, { total: 0, page: 1, page_size: 25, items: [] }));
    wrap(<AdminReviewsPage />);
    const u = userEvent.setup();
    await u.selectOptions(await screen.findByLabelText("Status"), "hidden");
    await u.selectOptions(screen.getByLabelText("Type"), "host_to_guest");
    await u.type(screen.getByLabelText("Search"), "mess");
    await waitFor(() => {
      const q = new URL(calls.at(-1)!.url, "http://x").searchParams;
      expect([q.get("status"), q.get("direction"), q.get("q")]).toEqual(["hidden", "host_to_guest", "mess"]);
    });
  });
});

describe("admin issues", () => {
  const issue: AdminIssueRow = { id: "i1", booking_id: "b1", reference: "NB-ABCD2345", property: "Beach House", role: "guest", category: "safety", body: "The balcony railing was loose and unsafe.", status: "open", resolution: null,
    created_at: "2099-01-01T00:00:00Z", resolved_at: null, admin_note: null, blocks_payout: false, updated_at: "x", booking_status: "confirmed",
    reporter: { name: "Gina Guest", email: "gina@example.com" }, guest: { name: "Gina Guest", email: "gina@example.com" }, host: { name: "Hannah", email: "h@example.com" } };
  beforeEach(() => stub((url, init) => (init?.method === "PATCH" ? json(200, issue) : json(200, { total: 1, page: 1, page_size: 25, items: [issue] }))));

  it("shows who, what and which booking, and by default only what needs work", async () => {
    wrap(<AdminIssuesPage />);
    expect(await screen.findByText("The balcony railing was loose and unsafe.")).toBeTruthy();
    expect(screen.getByText(/reporter Gina Guest \(gina@example\.com\)/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "NB-ABCD2345" }).getAttribute("href")).toBe("/admin/bookings/b1");
    expect(new URL(calls[0].url, "http://x").searchParams.get("status")).toBe("open_work");
  });

  it("starts work, and flags the payout hold with one click", async () => {
    wrap(<AdminIssuesPage />);
    const u = userEvent.setup();
    await u.type(await screen.findByLabelText(/Internal note/), "Called the host");
    await u.click(screen.getByRole("button", { name: "Start working on it" }));
    await waitFor(() => expect(sent("/admin/issues/i1")).toEqual({ status: "in_progress", admin_note: "Called the host" }));
    await u.click(screen.getByLabelText(/Hold the host payout/));
    await waitFor(() => expect(sent("/admin/issues/i1")).toEqual({ blocks_payout: true }));
  });

  it("will not resolve or dismiss without a message for the reporter", async () => {
    wrap(<AdminIssuesPage />);
    const u = userEvent.setup();
    const resolve = (await screen.findByRole("button", { name: "Resolve" })) as HTMLButtonElement;
    const dismiss = screen.getByRole("button", { name: "Dismiss" }) as HTMLButtonElement;
    expect(resolve.disabled).toBe(true);
    expect(dismiss.disabled).toBe(true);
    await u.type(screen.getByLabelText(/Message to the reporter/), "The railing has been repaired.");
    await u.click(resolve);
    await waitFor(() => expect(sent("/admin/issues/i1")).toEqual({ status: "resolved", resolution: "The railing has been repaired.", admin_note: "" }));
  });

  it("shows the outcome and no controls for a closed issue", async () => {
    stub(() => json(200, { total: 1, page: 1, page_size: 25, items: [{ ...issue, status: "resolved", resolution: "All sorted.", admin_note: "Inspected on site" }] }));
    wrap(<AdminIssuesPage />);
    expect(await screen.findByText(/All sorted\./)).toBeTruthy();
    expect(screen.getByText(/Inspected on site/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Resolve" })).toBeNull();
  });

  it("shows a payout-hold badge on a flagged open issue", async () => {
    stub(() => json(200, { total: 1, page: 1, page_size: 25, items: [{ ...issue, blocks_payout: true }] }));
    wrap(<AdminIssuesPage />);
    expect(await screen.findByText("payout on hold")).toBeTruthy();
  });
});
