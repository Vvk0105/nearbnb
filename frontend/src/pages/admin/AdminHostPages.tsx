import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl } from "../../lib/api";
import { label } from "../../lib/format";
import type { HostInfo } from "../../lib/types";
import { Alert, Button, Card, Select, StatusBadge, TextArea, Field, errorMessage } from "../../components/ui";

type HostRow = { id: string; state: string; email: string; full_name: string; submitted_at: string | null; created_at: string };
type OverrideRow = { key: string; label: string; unit: string; type: string; choices: (string | number)[]; global_value: unknown; value: unknown; overridden: boolean };
type HostDetail = HostInfo & { user: { id: string; email: string; full_name: string; phone: string | null }; overrides: OverrideRow[] };

export function HostsListPage() {
  const [state, setState] = useState("pending_review");
  const [q, setQ] = useState("");
  const { data, error, isLoading } = useQuery({
    queryKey: ["admin-hosts", state, q],
    queryFn: () => api<{ total: number; items: HostRow[] }>(`/admin/hosts?page_size=100${state ? `&state=${state}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  });
  return (
    <Card title="Hosts">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Select id="state" label="Show" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="pending_review">Waiting for review</option>
          <option value="changes_requested">Changes requested</option>
          <option value="approved">Approved, payout setup pending</option>
          <option value="active">Active</option>
          <option value="restricted">Restricted</option>
          <option value="suspended">Suspended</option>
          <option value="rejected">Rejected</option>
          <option value="draft">Drafts</option>
          <option value="">Everyone</option>
        </Select>
        <Field id="q" label="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <table className="w-full text-left text-sm">
        <thead className="border-b text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Host</th><th className="pr-3">Email</th><th className="pr-3">Status</th><th>Submitted</th></tr></thead>
        <tbody>
          {data?.items.map((h) => (
            <tr key={h.id} className="border-b border-gray-100">
              <td className="py-2 pr-3"><Link className="text-rose-600 hover:underline" to={`/admin/hosts/${h.id}`}>{h.full_name}</Link></td>
              <td className="pr-3">{h.email}</td>
              <td className="pr-3"><StatusBadge status={h.state} /></td>
              <td>{h.submitted_at ? new Date(h.submitted_at).toLocaleString() : "-"}</td>
            </tr>
          ))}
          {data?.items.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-gray-500">Nobody here.</td></tr>}
        </tbody>
      </table>
    </Card>
  );
}

export function HostDetailPage() {
  const { hostId } = useParams();
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["admin-host", hostId], queryFn: () => api<HostDetail>(`/admin/hosts/${hostId}`) });
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return <p className="text-gray-500">Loading...</p>;

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-host", hostId] });
  const act = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: "success", text: success });
      setNote("");
      setReason("");
      await refresh();
      void queryClient.invalidateQueries({ queryKey: ["admin-hosts"] });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };
  const decide = (decision: string, text: string) => act(() => api(`/admin/hosts/${hostId}/decision`, { method: "POST", body: { decision, note } }), text);

  const app = data.application ?? {};
  const parseOverride = (o: OverrideRow, text: string): unknown => (o.type === "int" ? Number.parseInt(text, 10) : o.type === "float" ? Number.parseFloat(text) : o.choices.find((c) => String(c) === text) ?? text);

  return (
    <div className="space-y-6">
      <div>
        <Link to="/admin/hosts" className="text-sm text-rose-600 hover:underline">← Hosts</Link>
        <div className="mt-1 flex items-center gap-3"><h1 className="text-2xl font-bold">{data.user.full_name}</h1><StatusBadge status={data.state} /></div>
        <p className="text-sm text-gray-600">{data.user.email}{data.user.phone ? ` · ${data.user.phone}` : ""}</p>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}

      <Card title="Application">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          {Object.entries(app).map(([k, v]) => (<div key={k}><dt className="text-xs text-gray-500">{label(k)}</dt><dd>{v}</dd></div>))}
        </dl>
        {data.review_note && <p className="mt-3 text-sm text-gray-600">Last note: {data.review_note}</p>}
      </Card>

      <Card title="Identity documents">
        <p className="mb-3 text-xs text-gray-500">Every time you open one of these files it is recorded in the audit log.</p>
        <ul className="space-y-2">
          {data.documents?.map((d) => (
            <li key={d.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-sm">
              <span><strong>{label(d.doc_type)}</strong> · {d.original_name} · {(d.size_bytes / 1024).toFixed(0)} KB</span>
              <a className="text-rose-600 hover:underline" href={apiUrl(`/admin/hosts/${hostId}/documents/${d.id}/file`)} target="_blank" rel="noreferrer noopener">View</a>
            </li>
          ))}
          {data.documents?.length === 0 && <li className="text-sm text-gray-500">No documents uploaded.</li>}
        </ul>
      </Card>

      {data.review_status === "pending_review" && (
        <Card title="Decision">
          <TextArea id="note" label="Message to the host" rows={3} value={note} onChange={(e) => setNote(e.target.value)} hint="Required when rejecting or requesting changes. The host receives this by email." />
          <div className="mt-4 flex flex-wrap gap-3">
            <Button loading={busy} onClick={() => decide("approve", "Approved. The host was emailed and can now set up payouts.")}>Approve</Button>
            <Button variant="secondary" loading={busy} onClick={() => decide("request_changes", "Changes requested.")}>Request changes</Button>
            <Button variant="danger" loading={busy} onClick={() => decide("reject", "Rejected.")}>Reject</Button>
          </div>
        </Card>
      )}

      <Card title="Payout setup (Stripe)">
        <ul className="space-y-1 text-sm">
          <li>{data.stripe?.connected ? "✓" : "○"} Account created {data.stripe?.account_id && <code className="ml-2 text-xs text-gray-500">{data.stripe.account_id}</code>}</li>
          <li>{data.stripe?.details_submitted ? "✓" : "○"} Details submitted</li>
          <li>{data.stripe?.payouts_enabled ? "✓" : "○"} Payouts enabled</li>
          <li>{data.stripe?.transfers_active ? "✓" : "○"} Transfers active</li>
        </ul>
        {data.stripe?.requirements && data.stripe.requirements.length > 0 && <p className="mt-2 text-sm text-gray-600">Stripe still needs: {data.stripe.requirements.map(label).join(", ")}</p>}
      </Card>

      <Card title="Account status">
        {data.state === "suspended" ? (
          <div className="space-y-3">
            <Alert>Suspended: {data.suspended_reason}</Alert>
            <Button loading={busy} onClick={() => act(() => api(`/admin/hosts/${hostId}/reactivate`, { method: "POST" }), "Host reactivated.")}>Reactivate host</Button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-gray-600">Suspending hides all of this host's listings and stops them from editing. Existing bookings are handled separately.</p>
            <Field id="reason" label="Reason (required, recorded in the audit log)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button variant="danger" loading={busy} onClick={() => act(() => api(`/admin/hosts/${hostId}/suspend`, { method: "POST", body: { reason } }), "Host suspended.")}>Suspend host</Button>
          </div>
        )}
      </Card>

      <Card title="Settings for this host">
        <p className="mb-3 text-sm text-gray-600">Override a platform setting for this host only. Anything not overridden follows the global value.</p>
        <table className="w-full text-left text-sm">
          <thead className="border-b text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Setting</th><th className="pr-3">Global</th><th className="pr-3">This host</th><th /></tr></thead>
          <tbody>
            {data.overrides.map((o) => {
              const text = drafts[o.key] ?? String(o.value);
              return (
                <tr key={o.key} className="border-b border-gray-100">
                  <td className="py-2 pr-3">{o.label}{o.unit && <span className="text-gray-400"> ({o.unit})</span>}</td>
                  <td className="pr-3">{String(o.global_value)}</td>
                  <td className="pr-3">
                    {o.choices.length > 0 ? (
                      <select className="rounded border border-gray-300 px-2 py-1" value={text} onChange={(e) => setDrafts({ ...drafts, [o.key]: e.target.value })}>
                        {o.choices.map((c) => <option key={String(c)}>{String(c)}</option>)}
                      </select>
                    ) : (
                      <input className="w-24 rounded border border-gray-300 px-2 py-1" value={text} onChange={(e) => setDrafts({ ...drafts, [o.key]: e.target.value })} />
                    )}
                    {o.overridden && <span className="ml-2 text-xs text-rose-600">overridden</span>}
                  </td>
                  <td className="space-x-2 whitespace-nowrap">
                    <button className="text-rose-600 hover:underline" onClick={() => act(() => api(`/admin/hosts/${hostId}/overrides/${o.key}`, { method: "PUT", body: { value: parseOverride(o, text) } }), `${o.label} set for this host.`)}>Set</button>
                    {o.overridden && <button className="text-gray-500 hover:underline" onClick={() => act(async () => { await api(`/admin/hosts/${hostId}/overrides/${o.key}`, { method: "DELETE" }); setDrafts({ ...drafts, [o.key]: String(o.global_value) }); }, `${o.label} reset to the global value.`)}>Reset</button>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
