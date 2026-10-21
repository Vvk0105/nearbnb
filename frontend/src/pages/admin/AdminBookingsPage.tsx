import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { BookingItem, BookingList } from "../../lib/types";
import { BookingBadge } from "../../components/bookings";
import { Pager } from "../../components/Pager";
import { Alert, Button, Card, Field, Select, TextArea, errorMessage } from "../../components/ui";

const STATUSES = ["", "requested", "accepted", "confirmed", "checked_in", "completed", "cancelled", "rejected", "expired", "withdrawn", "payment_expired"];

type AdminBooking = BookingItem & { host?: { id: string; name: string; email: string } };

function Actions({ b, onDone }: { b: AdminBooking; onDone: () => Promise<unknown> }) {
  const [mode, setMode] = useState<"" | "cancel" | "hold">("");
  const [reason, setReason] = useState("");
  const [refund, setRefund] = useState("policy");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (path: string, body?: unknown) => {
    setBusy(true);
    setError("");
    try {
      await api(`/admin/bookings/${b.id}/${path}`, { method: "POST", body });
      setMode("");
      setReason("");
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const p = b.payout;
  const canCancel = b.status === "confirmed" || b.status === "checked_in";
  return (
    <div className="space-y-1 text-xs">
      {p && p.state !== "none" && (
        <p>Payout {p.state}: {money(p.amount, b.currency)}{p.error ? <span className="block text-red-700">{p.error}</span> : null}{p.hold_note ? <span className="block text-gray-500">{p.hold_note}</span> : null}</p>
      )}
      {b.refund && <p>Refund {b.refund.state}: {money(b.refund.refunded + b.refund.pending + b.refund.failed, b.currency)}</p>}
      {error && <p className="text-red-700">{error}</p>}
      {mode === "" && (
        <div className="flex flex-wrap gap-2">
          {canCancel && <button className="text-red-700 underline" onClick={() => setMode("cancel")}>Cancel booking</button>}
          {p && (p.state === "scheduled" || p.state === "failed") && <button className="text-brand-700 underline" onClick={() => setMode("hold")}>Hold payout</button>}
          {p?.state === "held" && <button className="text-brand-700 underline" onClick={() => run("payout-hold", { hold: false })}>Release hold</button>}
          {p?.state === "failed" && <button className="text-brand-700 underline" onClick={() => run("payout-retry")}>Retry payout</button>}
        </div>
      )}
      {mode === "cancel" && (
        <div className="min-w-64 space-y-2 rounded-lg border border-red-200 bg-red-50 p-2">
          <Select id={`rf-${b.id}`} label="Refund" value={refund} onChange={(e) => setRefund(e.target.value)}>
            <option value="policy">By the booking's cancellation policy</option>
            <option value="full">Full refund</option>
          </Select>
          <TextArea id={`rs-${b.id}`} label="Reason (required, both sides are emailed)" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex gap-2">
            <Button variant="danger" loading={busy} disabled={!reason.trim()} onClick={() => run("cancel", { reason, refund })}>Cancel booking</Button>
            <Button variant="secondary" disabled={busy} onClick={() => setMode("")}>Back</Button>
          </div>
        </div>
      )}
      {mode === "hold" && (
        <div className="min-w-64 space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-2">
          <TextArea id={`hn-${b.id}`} label="Why is the payout on hold?" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex gap-2">
            <Button loading={busy} onClick={() => run("payout-hold", { hold: true, note: reason })}>Hold payout</Button>
            <Button variant="secondary" disabled={busy} onClick={() => setMode("")}>Back</Button>
          </div>
        </div>
      )}
    </div>
  );
}

export function AdminBookingsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), ...(status ? { status } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
  const { data, error, isLoading } = useQuery({
    queryKey: ["bookings", "admin", status, q, page],
    queryFn: () => api<BookingList>(`/admin/bookings?${params}`),
    refetchInterval: 30_000,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["bookings"] });
  return (
    <Card title="Bookings">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Field id="bq" label="Search" placeholder="Reference, guest, host or property" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <Select id="bstatus" label="Show" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "All"}</option>)}
        </Select>
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="overflow-x-auto">
        <div className="overflow-x-auto"><table className="data-table">
          <thead className="text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">Ref</th><th className="pr-3">Place</th><th className="pr-3">Guest</th><th className="pr-3">Host</th><th className="pr-3">Stay</th><th className="pr-3">Total</th><th className="pr-3">Payment</th><th className="pr-3">Status</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {(data?.items as AdminBooking[] | undefined)?.map((b) => (
              <tr key={b.id} className="border-t border-gray-100 align-top">
                <td className="py-2 pr-3 font-mono text-xs"><Link className="text-brand-700 underline" to={`/admin/bookings/${b.id}`}>{b.reference}</Link></td>
                <td className="pr-3">{b.property.name}<br /><span className="text-xs text-gray-500">{b.unit.name}{b.room ? ` · room ${b.room}` : ""}</span></td>
                <td className="pr-3">{b.guest?.full_name}<br /><span className="text-xs text-gray-500">{b.guest?.email}</span></td>
                <td className="pr-3">{b.host?.name}<br /><span className="text-xs text-gray-500">{b.host?.email}</span></td>
                <td className="pr-3 whitespace-nowrap">{b.check_in_label}<br />→ {b.check_out_label}</td>
                <td className="pr-3">{money(b.price.total ?? 0, b.currency)}</td>
                <td className="pr-3 text-xs">{b.payment ? <>{b.payment.status}{b.payment.failure_code ? ` (${b.payment.failure_code})` : ""}<br /><span className="font-mono text-gray-500">{b.payment.intent_id?.slice(0, 18)}</span></> : "none"}{b.invoice_number ? <><br />{b.invoice_number}</> : null}</td>
                <td className="pr-3"><BookingBadge status={b.status} as="host" />{b.cancelled && <span className="mt-1 block text-xs text-gray-500">by {b.cancelled.by}{b.cancelled.reason ? `: ${b.cancelled.reason}` : ""}</span>}</td>
                <td><Actions b={b} onDone={refresh} /></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No bookings.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}
