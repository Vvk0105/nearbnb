import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { fromCents, money, toCents } from "../../lib/format";
import type { RefundRequestItem, RefundRow } from "../../lib/types";
import { Alert, Button, Card, Field, Select, TextArea, errorMessage } from "../../components/ui";

function RequestCard({ r, onDone }: { r: RefundRequestItem; onDone: () => Promise<unknown> }) {
  const [amount, setAmount] = useState(fromCents(r.amount));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const decide = async (decision: "approve" | "reject") => {
    setBusy(true);
    setError("");
    try {
      await api(`/admin/refund-requests/${r.id}/decision`, { method: "POST", body: { decision, note: note.trim() || null, ...(decision === "approve" ? { amount: toCents(amount) } : {}) } });
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const cur = r.currency ?? "AUD";
  return (
    <div className="space-y-3 border-b border-gray-100 py-4 last:border-0">
      <p className="text-sm"><strong>{r.reference}</strong> · {r.property} · host {r.host}</p>
      <p className="text-sm">Asked for <strong>{money(r.amount, cur)}</strong> of the {money(r.paid ?? 0, cur)} the guest paid.</p>
      <p className="whitespace-pre-wrap rounded-lg bg-gray-50 p-3 text-sm">{r.reason}</p>
      {error && <Alert>{error}</Alert>}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id={`ra-${r.id}`} label={`Amount to refund (${cur})`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} hint="You may grant less than asked." />
        <div className="sm:col-span-2"><TextArea id={`rn-${r.id}`} label="Note to the host (required to reject)" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></div>
      </div>
      <div className="flex gap-2">
        <Button loading={busy} onClick={() => decide("approve")}>Approve and refund</Button>
        <Button variant="danger" loading={busy} onClick={() => decide("reject")}>Reject</Button>
      </div>
    </div>
  );
}

export function AdminRefundsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("failed");
  const [retryError, setRetryError] = useState("");
  const requests = useQuery({ queryKey: ["admin-refund-requests"], queryFn: () => api<RefundRequestItem[]>("/admin/refund-requests?status=pending"), refetchInterval: 30_000 });
  const refunds = useQuery({ queryKey: ["admin-refunds", status], queryFn: () => api<RefundRow[]>(`/admin/refunds${status ? `?status=${status}` : ""}`), refetchInterval: 30_000 });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin-refund-requests"] });
    await queryClient.invalidateQueries({ queryKey: ["admin-refunds"] });
  };
  const retry = async (id: string) => {
    setRetryError("");
    try {
      await api(`/admin/refunds/${id}/retry`, { method: "POST" });
      await refresh();
    } catch (e) {
      setRetryError(errorMessage(e));
    }
  };
  return (
    <div className="space-y-6">
      <Card title="Refund requests from hosts">
        {requests.error && <Alert>{errorMessage(requests.error)}</Alert>}
        {requests.data?.length === 0 && <p className="text-sm text-gray-500">Nothing waiting for review.</p>}
        {requests.data?.map((r) => <RequestCard key={r.id} r={r} onDone={refresh} />)}
      </Card>
      <Card title="Refunds">
        <div className="mb-4 max-w-xs">
          <Select id="refund-status" label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="failed">Failed (needs attention)</option>
            <option value="pending">Waiting for Stripe</option>
            <option value="succeeded">Completed</option>
            <option value="">All</option>
          </Select>
        </div>
        {retryError && <Alert>{retryError}</Alert>}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Booking</th><th className="pr-3">Type</th><th className="pr-3">Amount</th><th className="pr-3">Status</th><th>Detail</th></tr></thead>
            <tbody>
              {refunds.data?.map((r) => (
                <tr key={r.id} className="border-t border-gray-100 align-top">
                  <td className="py-2 pr-3 font-mono text-xs">{r.reference}</td>
                  <td className="pr-3">{r.type.replace("_", " ")}</td>
                  <td className="pr-3">{money(r.amount, r.currency)}{r.host_reversal_amount > 0 && <span className="block text-xs text-gray-500">host gave back {money(r.host_reversal_amount, r.currency)}</span>}</td>
                  <td className="pr-3">{r.status}</td>
                  <td className="text-xs text-gray-600">
                    {r.error && <span className="block text-red-700">{r.error}</span>}
                    {r.status === "failed" && <button className="text-brand-700 underline" onClick={() => retry(r.id)}>Retry</button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {refunds.data?.length === 0 && <p className="py-4 text-center text-sm text-gray-500">No refunds here.</p>}
      </Card>
    </div>
  );
}
