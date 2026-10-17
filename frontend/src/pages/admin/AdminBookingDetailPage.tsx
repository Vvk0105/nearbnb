import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { AdminBookingDetail } from "../../lib/types";
import { BookingBadge } from "../../components/bookings";
import { Alert, Card, errorMessage } from "../../components/ui";

const t = (iso: string) => iso.slice(0, 16).replace("T", " ");

/** Everything about one booking on one page: who, when, money, payments, refunds, payout, and the history. */
export function AdminBookingDetailPage() {
  const { bookingId } = useParams();
  const { data: b, error, isLoading } = useQuery({ queryKey: ["admin-booking", bookingId], queryFn: () => api<AdminBookingDetail>(`/admin/bookings/${bookingId}`) });
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !b) return <Alert>{errorMessage(error)}</Alert>;
  const cur = b.currency;
  const snap = b.price as Record<string, number>;
  return (
    <div className="space-y-6">
      <Link to="/admin/bookings" className="text-sm text-brand-700 hover:underline">← Bookings</Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold font-mono">{b.reference}</h1>
        <BookingBadge status={b.status} as="host" />
        {b.invoice && <span className="text-sm text-gray-500">Invoice {b.invoice.number}</span>}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Stay">
          <p className="font-medium">{b.property.name} · {b.unit.name}{b.room ? ` · room ${b.room}` : ""}</p>
          <p className="text-sm text-gray-700">{b.check_in_label} → {b.check_out_label} ({b.hours} hours, {b.guests} guest{b.guests > 1 ? "s" : ""})</p>
          <p className="text-xs text-gray-500">Times local to the property ({b.timezone}).</p>
          <p className="mt-2 text-sm">Guest: <Link className="text-brand-700 underline" to={`/admin/users?q=${encodeURIComponent(b.guest?.email ?? "")}`}>{b.guest?.full_name}</Link> ({b.guest?.email}, {b.guest?.phone ?? "no phone"})</p>
          <p className="text-sm">Host: {(b as unknown as { host?: { name: string; email: string } }).host?.name} ({(b as unknown as { host?: { email: string } }).host?.email})</p>
          {b.cancelled && <p className="mt-2 text-sm text-red-800">Cancelled by {b.cancelled.by}{b.cancelled.reason ? `: ${b.cancelled.reason}` : ""}</p>}
        </Card>
        <Card title="Money">
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between"><dt>Accommodation</dt><dd>{money(snap.accommodation, cur)}</dd></div>
            <div className="flex justify-between"><dt>Cleaning fee</dt><dd>{money(snap.cleaning_fee, cur)}</dd></div>
            <div className="flex justify-between"><dt>Service fee</dt><dd>{money(snap.service_fee, cur)}</dd></div>
            <div className="flex justify-between"><dt>Tax</dt><dd>{money(snap.tax, cur)}</dd></div>
            <div className="flex justify-between border-t border-gray-200 pt-1 font-semibold"><dt>Guest paid</dt><dd>{money(snap.total, cur)}</dd></div>
            <div className="flex justify-between text-gray-600"><dt>Platform commission ({snap.commission_percent}%)</dt><dd>{money(snap.commission, cur)}</dd></div>
            <div className="flex justify-between text-gray-600"><dt>Host share</dt><dd>{money(snap.host_payable, cur)}</dd></div>
          </dl>
          {b.payout && b.payout.state !== "none" && <p className="mt-2 text-sm">Payout: <strong>{b.payout.state}</strong> {money(b.payout.amount, cur)}{b.payout.error && <span className="text-red-700"> ({b.payout.error})</span>}</p>}
        </Card>
      </div>

      <Card title="Timeline">
        <ol className="space-y-1 text-sm">
          <li className="text-gray-500">{t(b.requested_at ?? "")} requested</li>
          {b.timeline.map((e, i) => (
            <li key={i}><span className="text-gray-500">{t(e.at)}</span> {e.from?.replace(/_/g, " ")} → <strong>{e.to.replace(/_/g, " ")}</strong>{e.actor ? <span className="text-gray-500"> by {e.actor}</span> : <span className="text-gray-500"> (automatic)</span>}{e.note && <span className="text-gray-500"> · {e.note}</span>}</li>
          ))}
        </ol>
      </Card>

      <Card title="Payments">
        {b.payments.length === 0 ? <p className="text-sm text-gray-500">No payment started.</p> : (
          <ul className="space-y-1 text-sm">
            {b.payments.map((p) => <li key={p.id}>{t(p.created_at)} · {money(p.amount, cur)} · <strong>{p.status}</strong> (attempt {p.attempt}) <code className="text-xs text-gray-500">{p.intent_id}</code>{p.failure_message && <span className="text-red-700"> {p.failure_message}</span>}</li>)}
          </ul>
        )}
      </Card>

      <Card title="Refunds">
        {b.refunds.length === 0 && b.refund_requests.length === 0 ? <p className="text-sm text-gray-500">No refunds.</p> : (
          <ul className="space-y-1 text-sm">
            {b.refunds.map((r) => <li key={r.id}>{t(r.created_at)} · {r.type.replace("_", " ")} · {money(r.amount, cur)} · <strong>{r.status}</strong>{r.host_reversal_amount > 0 && ` · host gave back ${money(r.host_reversal_amount, cur)}`}{r.error && <span className="text-red-700"> {r.error}</span>}</li>)}
            {b.refund_requests.map((r) => <li key={r.id}>Host request for {money(r.amount, cur)}: <strong>{r.status}</strong> · {r.reason}{r.note && ` · note: ${r.note}`}</li>)}
          </ul>
        )}
        <Link to="/admin/refunds" className="mt-2 inline-block text-sm text-brand-700 underline">Open refunds</Link>
      </Card>

      <Card title="Audit trail">
        <ul className="space-y-1 text-sm">
          {b.audit.map((a, i) => <li key={i}><span className="text-gray-500">{t(a.at)}</span> <code className="text-xs">{a.action}</code>{a.actor && <span className="text-gray-500"> by {a.actor}</span>}{a.reason && <span className="text-gray-500"> · {a.reason}</span>}</li>)}
          {b.audit.length === 0 && <li className="text-gray-500">Nothing recorded.</li>}
        </ul>
      </Card>
    </div>
  );
}
