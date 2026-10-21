import { useState } from "react";
import { useEffect } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, mediaUrl } from "../../lib/api";
import { money } from "../../lib/format";
import type { BookingGroup, BookingItem, BookingList, PriceBreakdown, Quote } from "../../lib/types";
import { PriceBreakdownView } from "../../components/availability/AvailabilityPanel";
import { BookingBadge, Countdown } from "../../components/bookings";
import { CancelBooking } from "../../components/CancelBooking";
import { ReportProblem, ReviewBox } from "../../components/Reviews";
import { Alert, Button, Card, errorMessage } from "../../components/ui";
import { EmptyState } from "../../components/dashboardUi";
import { Icon } from "../../components/icons";

const TABS: ["all" | BookingGroup | "cancelled", string][] = [
  ["all", "All"], ["pending", "Waiting for host"], ["awaiting_payment", "Awaiting payment"], ["upcoming", "Upcoming"], ["past", "Past"], ["cancelled", "Cancelled"],
];

function TripCard({ b }: { b: BookingItem }) {
  return (
    <Link to={`/account/trips/${b.id}`} className="group block overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:border-brand-300 hover:shadow-md">
      <div className="flex flex-col sm:flex-row">
        {b.property.cover_url ? (
          <img src={mediaUrl(b.property.cover_url)} alt="" className="h-36 w-full shrink-0 object-cover sm:h-auto sm:w-44" />
        ) : (
          <div className="flex h-36 w-full shrink-0 items-center justify-center bg-slate-100 text-slate-300 sm:h-auto sm:w-44"><Icon name="home" className="h-8 w-8" /></div>
        )}
        <div className="min-w-0 flex-1 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate text-base font-semibold">{b.property.name}</h3>
            <BookingBadge status={b.status} />
          </div>
          <p className="text-sm text-slate-600">{b.unit.name} · {[b.property.suburb, b.property.state].filter(Boolean).join(", ")}</p>
          <p className="mt-2 flex items-center gap-2 text-sm text-slate-800"><Icon name="calendar" className="h-4 w-4 text-slate-400" />{b.check_in_label} → {b.check_out_label}</p>
          <p className="mt-1 text-xs text-slate-500">{b.guests} guest{b.guests > 1 ? "s" : ""} · {money(b.price.total ?? 0, b.currency)} · Ref {b.reference}</p>
          {b.status === "requested" && <Countdown target={b.expires_at} seconds={b.seconds_to_respond} label="Host replies within" />}
          {b.status === "accepted" && <Countdown target={b.payment_deadline_at} seconds={b.seconds_to_pay} label="Pay within" />}
        </div>
      </div>
    </Link>
  );
}

export function TripsPage() {
  const [group, setGroup] = useState<"all" | BookingGroup | "cancelled">("all");
  const { data, error, isLoading } = useQuery({
    queryKey: ["bookings", "guest", group],
    queryFn: () => api<BookingList>(`/me/bookings${group === "all" ? "" : group === "cancelled" ? "?status=cancelled" : `?group=${group}`}`),
    refetchInterval: 30_000,
  });
  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <h1 className="text-2xl font-bold tracking-tight">My trips</h1>
      <div className="flex gap-2 overflow-x-auto pb-1" role="tablist">
        {TABS.map(([key, text]) => (
          <button key={key} role="tab" aria-selected={group === key} onClick={() => setGroup(key)}
            className={`shrink-0 rounded-full border px-3.5 py-1.5 text-sm transition ${group === key ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
            {text}{key !== "all" && key !== "cancelled" && data?.counts[key] ? ` (${data.counts[key]})` : ""}
          </button>
        ))}
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-slate-500">Loading...</p>}
      {data && data.items.length === 0 && (
        <EmptyState icon="trip" title="No trips here yet">
          <Link to="/search" className="text-brand-700 underline">Find a place to stay</Link>
        </EmptyState>
      )}
      <div className="space-y-4">{data?.items.map((b) => <TripCard key={b.id} b={b} />)}</div>
    </div>
  );
}

/** Where a normal booking is on its way: asked, accepted, paid, staying, done. Cancelled and expired ones show a message instead. */
const STEPS = ["Requested", "Accepted", "Paid", "Staying", "Completed"];
const STEP_AT: Partial<Record<BookingItem["status"], number>> = { requested: 0, accepted: 1, payment_pending: 1, confirmed: 2, checked_in: 3, completed: 4 };

export function TripProgress({ status }: { status: BookingItem["status"] }) {
  const at = STEP_AT[status];
  if (at === undefined) return null;
  return (
    <ol aria-label="Trip progress" className="flex items-center">
      {STEPS.map((label, i) => (
        <li key={label} className="flex flex-1 items-center last:flex-none">
          <span className="flex flex-col items-center gap-1">
            <span aria-current={i === at ? "step" : undefined} className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${i < at ? "bg-brand-600 text-white" : i === at ? "bg-brand-600 text-white ring-4 ring-brand-100" : "bg-slate-200 text-slate-500"}`}>
              {i < at ? <Icon name="check" className="h-3.5 w-3.5" /> : i + 1}
            </span>
            <span className={`text-[11px] ${i <= at ? "font-medium text-slate-800" : "text-slate-400"}`}>{label}</span>
          </span>
          {i < STEPS.length - 1 && <span aria-hidden="true" className={`mx-1 mb-5 h-0.5 flex-1 ${i < at ? "bg-brand-600" : "bg-slate-200"}`} />}
        </li>
      ))}
    </ol>
  );
}

export function TripDetailPage() {
  const { bookingId } = useParams();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [params] = useSearchParams();
  const { data: b, error, isLoading, refetch } = useQuery({
    queryKey: ["bookings", "guest", "one", bookingId],
    queryFn: () => api<BookingItem>(`/me/bookings/${bookingId}`),
    retry: false,
    // While a payment is going through, keep checking: the webhook (or our own check with Stripe) confirms it within seconds.
    refetchInterval: (q) => (q.state.data?.status === "payment_pending" && params.get("paid") ? 3000 : false),
  });

  // Back from the payment form: ask the server to read the payment from Stripe right away instead of waiting for the webhook.
  const returned = Boolean(params.get("paid")) || params.has("payment_intent");
  useEffect(() => {
    if (!returned || !bookingId) return;
    api(`/me/bookings/${bookingId}/pay/sync`, { method: "POST" }).then(() => refetch()).catch(() => undefined);
  }, [returned, bookingId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !b) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <div className="mx-auto max-w-xl space-y-3 py-10 text-center">
        <h1 className="text-xl font-bold">{missing ? "Booking not found" : "Something went wrong"}</h1>
        <Link to="/account/trips" className="text-brand-700 hover:underline">Back to my trips</Link>
      </div>
    );
  }

  const withdraw = async () => {
    setBusy(true);
    setActionError("");
    try {
      await api(`/me/bookings/${b.id}/withdraw`, { method: "POST" });
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
      setConfirming(false);
    } catch (e) {
      setActionError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div>
        <Link to="/account/trips" className="text-sm text-brand-700 hover:underline">← My trips</Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-bold tracking-tight md:text-3xl">{b.property.name}</h1>
          <BookingBadge status={b.status} />
        </div>
        <p className="mt-1 text-sm text-slate-600">{b.unit.name} · {[b.property.suburb, b.property.state].filter(Boolean).join(", ")} · Reference {b.reference}</p>
      </div>
      {STEP_AT[b.status] !== undefined && <div className="rounded-2xl border border-slate-200 bg-white px-4 py-5 shadow-sm sm:px-8"><TripProgress status={b.status} /></div>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
        {b.status === "requested" && (
          <Alert kind="info">
            Your request is with {b.host?.first_name ?? "the host"}. You have not been charged.
            <Countdown target={b.expires_at} seconds={b.seconds_to_respond} label="They reply within" onDone={() => void refetch()} />
          </Alert>
        )}
        {b.status === "accepted" && (
          <Alert kind="success">
            {b.host?.first_name ?? "The host"} accepted your request. Pay before the time runs out to keep the dates.
            <Countdown target={b.payment_deadline_at} seconds={b.seconds_to_pay} label="Pay within" onDone={() => void refetch()} />
          </Alert>
        )}
        {b.status === "payment_pending" && (
          <Alert kind="info">
            {returned ? "Checking your payment with Stripe..." : "Your payment has been started but is not finished."}
            <Countdown target={b.payment_deadline_at} seconds={b.seconds_to_pay} label="Pay within" onDone={() => void refetch()} />
          </Alert>
        )}
        {b.payment?.status === "failed" && b.payment.failure_message && b.can_pay && <Alert>Your last payment did not go through: {b.payment.failure_message} You can try another card.</Alert>}
        {b.status === "confirmed" && <Alert kind="success">Payment received. Your stay is confirmed. A confirmation email is on its way.</Alert>}
        {b.can_pay && (
          <Link to={`/account/trips/${b.id}/pay`}><Button>{b.status === "payment_pending" ? "Continue to payment" : "Pay now"} · {money(b.price.total ?? 0, b.currency)}</Button></Link>
        )}
        {b.status === "rejected" && <Alert>The host declined this request.{b.closed_reason ? ` Their message: "${b.closed_reason}"` : ""} You were not charged.</Alert>}
        {b.status === "expired" && <Alert>The host did not reply in time, so this request expired. You were not charged.</Alert>}
        {b.status === "payment_expired" && <Alert>Payment was not completed in time, so the dates were released. You were not charged.</Alert>}
        {b.status === "withdrawn" && <Alert kind="info">You withdrew this request.</Alert>}
        {b.status === "cancelled" && (
          <Alert kind="info">
            {b.cancelled?.by === "guest" ? "You cancelled this booking." : b.cancelled?.by === "host" ? "The host cancelled this booking." : "NearBnB cancelled this booking."}
            {b.cancelled?.reason ? ` Reason: "${b.cancelled.reason}".` : ""}
            {b.refund?.state === "refunded" && ` ${money(b.refund.refunded, b.currency)} has been refunded to your card (it can take 5 to 10 days to appear).`}
            {b.refund?.state === "pending" && ` Your refund of ${money(b.refund.pending, b.currency)} is being processed.`}
            {b.refund?.state === "failed" && " We could not send your refund automatically. Our team has been alerted and will fix it."}
            {!b.refund && " No refund was due under the cancellation policy."}
          </Alert>
        )}
        {b.status !== "cancelled" && b.refund && b.refund.state !== "failed" && <Alert kind="success">A refund of {money(b.refund.refunded + b.refund.pending, b.currency)} was issued for this booking.</Alert>}

        {b.checkin_instructions && (
          <Card title="Check-in instructions"><p className="whitespace-pre-wrap text-sm">{b.checkin_instructions}</p></Card>
        )}
        {b.host?.email && (
          <Card title="Your host"><p className="text-sm">{b.host.name}<br />{b.host.email}{b.host.phone ? <><br />{b.host.phone}</> : null}</p></Card>
        )}

        {b.status === "completed" && <ReviewBox booking={b} as="guest" />}
        <ReportProblem booking={b} as="guest" />
        {b.status === "confirmed" && <CancelBooking booking={b} />}
        {b.can_withdraw && (
          <div className="space-y-2">
            {actionError && <Alert>{actionError}</Alert>}
            {!confirming ? (
              <Button variant="secondary" onClick={() => setConfirming(true)}>Withdraw this request</Button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-sm">Withdraw and release the dates?</span>
                <Button variant="danger" onClick={withdraw} loading={busy}>Yes, withdraw</Button>
                <Button variant="secondary" onClick={() => setConfirming(false)} disabled={busy}>Keep it</Button>
              </div>
            )}
          </div>
        )}
        </div>
        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
        <Card title="Your stay">
          <dl className="grid gap-3 text-sm sm:grid-cols-2">
            <div><dt className="text-gray-500">Check-in</dt><dd className="font-medium">{b.check_in_label}</dd></div>
            <div><dt className="text-gray-500">Check-out</dt><dd className="font-medium">{b.check_out_label}</dd></div>
            <div><dt className="text-gray-500">Length</dt><dd className="font-medium">{b.hours} hours</dd></div>
            <div><dt className="text-gray-500">Guests</dt><dd className="font-medium">{b.guests}</dd></div>
          </dl>
          <p className="mt-3 text-xs text-gray-500">Times are local to the property ({b.timezone.replace("_", " ")}).</p>
        </Card>

        <Card title="Price">
          <PriceBreakdownView quote={{ price: b.price as PriceBreakdown, currency: b.currency } as Quote} />
        </Card>

        {b.invoice_number && (
          <Card title="Receipt"><p className="text-sm">Invoice {b.invoice_number}. <Link to={`/account/trips/${b.id}/invoice`} className="font-medium text-brand-700 underline">View receipt and tax invoice</Link></p></Card>
        )}
        </aside>
      </div>
    </div>
  );
}
