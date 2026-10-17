import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl } from "../../lib/api";
import { money } from "../../lib/format";
import type { AdminPaymentRow, AdminPayoutRow, Paged, WebhookRow } from "../../lib/types";
import { Pager } from "../../components/Pager";
import { Alert, Button, Card, Field, Select, errorMessage } from "../../components/ui";

export function AdminPaymentsPage() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), ...(status ? { status } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-payments", status, q, page], queryFn: () => api<Paged<AdminPaymentRow>>(`/admin/payments?${params}`) });
  return (
    <Card title="Payments">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Select id="pstatus" label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">All</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option><option value="created">Started</option><option value="processing">Processing</option><option value="cancelled">Cancelled</option>
        </Select>
        <Field id="pq" label="Search" placeholder="Booking reference, guest email or Stripe id" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Booking</th><th className="pr-3">Guest</th><th className="pr-3">Amount</th><th className="pr-3">Status</th><th className="pr-3">Stripe</th><th>When</th></tr></thead>
          <tbody>
            {data?.items.map((p) => (
              <tr key={p.id} className="border-t border-gray-100 align-top">
                <td className="py-2 pr-3"><Link className="font-mono text-xs text-brand-700 underline" to={`/admin/bookings/${p.booking_id}`}>{p.reference}</Link></td>
                <td className="pr-3">{p.guest}</td>
                <td className="pr-3">{money(p.amount, p.currency)}</td>
                <td className="pr-3">{p.status}{p.attempt > 1 && <span className="text-xs text-gray-500"> (attempt {p.attempt})</span>}{p.failure_message && <span className="block text-xs text-red-700">{p.failure_code}: {p.failure_message}</span>}</td>
                <td className="pr-3 font-mono text-xs text-gray-500">{p.intent_id}{p.charge_id && <><br />{p.charge_id}</>}</td>
                <td className="text-xs text-gray-500">{(p.succeeded_at ?? p.created_at).slice(0, 16).replace("T", " ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No payments match.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}

const PAYOUT_TEXT: Record<string, string> = { scheduled: "Scheduled", held: "On hold", paid: "Paid", failed: "Failed", reversed: "Reversed", none: "-" };

export function AdminPayoutsPage() {
  const queryClient = useQueryClient();
  const [state, setState] = useState("");
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const { data, error: loadError, isLoading } = useQuery({
    queryKey: ["admin-payouts", state, page],
    queryFn: () => api<Paged<AdminPayoutRow>>(`/admin/payouts?page=${page}${state ? `&state=${state}` : ""}`),
  });
  const act = async (bookingId: string, path: string, body?: unknown) => {
    setError("");
    try {
      await api(`/admin/bookings/${bookingId}/${path}`, { method: "POST", body });
      await queryClient.invalidateQueries({ queryKey: ["admin-payouts"] });
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  return (
    <Card title="Host payouts">
      <div className="mb-4 max-w-xs">
        <Select id="ostate" label="Show" value={state} onChange={(e) => { setState(e.target.value); setPage(1); }}>
          <option value="">All</option><option value="scheduled">Scheduled</option><option value="held">On hold</option><option value="failed">Failed</option><option value="paid">Paid</option><option value="reversed">Reversed</option>
        </Select>
      </div>
      {(error || loadError) && <Alert>{error || errorMessage(loadError)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Booking</th><th className="pr-3">Host</th><th className="pr-3">Amount</th><th className="pr-3">State</th><th className="pr-3">Due</th><th>Actions</th></tr></thead>
          <tbody>
            {data?.items.map((p) => (
              <tr key={p.booking_id} className="border-t border-gray-100 align-top">
                <td className="py-2 pr-3"><Link className="font-mono text-xs text-brand-700 underline" to={`/admin/bookings/${p.booking_id}`}>{p.reference}</Link></td>
                <td className="pr-3">{p.host}<br /><span className="text-xs text-gray-500">{p.host_email}</span></td>
                <td className="pr-3">{money(p.amount, p.currency)}{(p.reversed_amount ?? 0) > 0 && <span className="block text-xs text-gray-500">{money(p.reversed_amount ?? 0, p.currency)} taken back</span>}</td>
                <td className="pr-3">{PAYOUT_TEXT[p.state]}{p.error && <span className="block text-xs text-red-700">{p.error}</span>}{p.hold_note && <span className="block text-xs text-gray-500">{p.hold_note}</span>}</td>
                <td className="pr-3 text-xs text-gray-500">{p.state === "paid" ? p.transferred_at?.slice(0, 10) : p.eligible_at?.slice(0, 10)}</td>
                <td className="space-x-2 whitespace-nowrap text-xs">
                  {(p.state === "scheduled" || p.state === "failed") && <button className="text-brand-700 underline" onClick={() => act(p.booking_id, "payout-hold", { hold: true, note: "Held by admin" })}>Hold</button>}
                  {p.state === "held" && <button className="text-brand-700 underline" onClick={() => act(p.booking_id, "payout-hold", { hold: false })}>Release hold</button>}
                  {p.state === "failed" && <button className="text-brand-700 underline" onClick={() => act(p.booking_id, "payout-retry")}>Retry</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No payouts here.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}

export function AdminWebhooksPage() {
  const queryClient = useQueryClient();
  const [failedOnly, setFailedOnly] = useState(true);
  const [page, setPage] = useState(1);
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-webhooks", failedOnly, page], queryFn: () => api<Paged<WebhookRow>>(`/admin/webhooks?failed=${failedOnly}&page=${page}`) });
  const rerun = async (id: string) => {
    setMsg(null);
    try {
      const r = await api<{ processed: boolean; error: string | null }>(`/admin/webhooks/${id}/reprocess`, { method: "POST" });
      setMsg(r.processed ? { kind: "success", text: "Processed successfully." } : { kind: "error", text: `Still failing: ${r.error}` });
      await queryClient.invalidateQueries({ queryKey: ["admin-webhooks"] });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    }
  };
  return (
    <Card title="Stripe webhooks">
      <p className="mb-3 text-sm text-gray-600">Stripe tells us about payments, refunds and host account changes by sending these events. A failed one is safe to run again once the cause is fixed.</p>
      <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={failedOnly} onChange={(e) => { setFailedOnly(e.target.checked); setPage(1); }} /> Only show failed events</label>
      {msg && <div className="mb-3"><Alert kind={msg.kind}>{msg.text}</Alert></div>}
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <ul className="space-y-2">
        {data?.items.map((e) => (
          <li key={e.id} className="rounded-lg border border-gray-200 p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span><code>{e.type}</code> <span className="font-mono text-xs text-gray-500">{e.event_id}</span></span>
              <span className="text-xs text-gray-500">{e.created_at.slice(0, 16).replace("T", " ")} · {e.processed_at ? "processed" : e.error ? "failed" : "not processed"}</span>
            </div>
            {e.error && <p className="mt-1 text-xs text-red-700">{e.error}</p>}
            {!e.processed_at && <Button variant="secondary" className="mt-2" onClick={() => rerun(e.id)}>Run again</Button>}
          </li>
        ))}
      </ul>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">{failedOnly ? "No failed events." : "No events yet."}</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}

const EXPORTS: [string, string, string][] = [
  ["bookings", "Bookings", "Every request with guest, host, dates and the full price breakdown."],
  ["payments", "Payments", "Every card payment with Stripe references and failures."],
  ["refunds", "Refunds", "Every refund with its type, amount and Stripe outcome."],
  ["payouts", "Payouts", "Every host transfer with amounts, status and errors."],
];

export function AdminExportsPage() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const link = (kind: string) => apiUrl(`/admin/exports/${kind}.csv${from || to ? `?${new URLSearchParams({ ...(from ? { from } : {}), ...(to ? { to } : {}) })}` : ""}`);
  return (
    <div className="space-y-4">
      <Alert kind="info">Exports contain personal and financial details. Every download is recorded in the audit log. Leave the dates empty for everything.</Alert>
      <Card title="Date range (optional)">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="xfrom" label="From" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <Field id="xto" label="To (included)" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
      </Card>
      <div className="grid gap-4 sm:grid-cols-2">
        {EXPORTS.map(([kind, title, text]) => (
          <Card key={kind} title={title}>
            <p className="mb-3 text-sm text-gray-600">{text}</p>
            <a href={link(kind)} download className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">Download CSV</a>
          </Card>
        ))}
      </div>
    </div>
  );
}
