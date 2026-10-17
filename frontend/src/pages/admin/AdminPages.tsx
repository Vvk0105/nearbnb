import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { AuditEntry, SettingItem } from "../../lib/types";
import { Alert, Button, Card, Field, errorMessage } from "../../components/ui";

export function AdminLayout() {
  const tab = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-1.5 text-sm ${isActive ? "bg-brand-600 text-white" : "bg-white text-gray-700 border border-gray-200 hover:bg-gray-50"}`;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap gap-2">
        <NavLink to="/admin" end className={tab}>Dashboard</NavLink>
        <NavLink to="/admin/hosts" className={tab}>Hosts</NavLink>
        <NavLink to="/admin/users" className={tab}>Users</NavLink>
        <NavLink to="/admin/listings" className={tab}>Listings</NavLink>
        <NavLink to="/admin/bookings" className={tab}>Bookings</NavLink>
        <NavLink to="/admin/issues" className={tab}>Issues</NavLink>
        <NavLink to="/admin/reviews" className={tab}>Reviews</NavLink>
        <NavLink to="/admin/payments" className={tab}>Payments</NavLink>
        <NavLink to="/admin/payouts" className={tab}>Payouts</NavLink>
        <NavLink to="/admin/refunds" className={tab}>Refunds</NavLink>
        <NavLink to="/admin/cancellation" className={tab}>Cancellation</NavLink>
        <NavLink to="/admin/settings" className={tab}>Settings</NavLink>
        <NavLink to="/admin/catalogues" className={tab}>Types &amp; amenities</NavLink>
        <NavLink to="/admin/tax" className={tab}>Tax</NavLink>
        <NavLink to="/admin/legal" className={tab}>Legal documents</NavLink>
        <NavLink to="/admin/audit" className={tab}>Audit log</NavLink>
        <NavLink to="/admin/webhooks" className={tab}>Webhooks</NavLink>
        <NavLink to="/admin/exports" className={tab}>Exports</NavLink>
      </div>
      <Outlet />
    </div>
  );
}

function parseInput(item: SettingItem, text: string): unknown {
  switch (item.type) {
    case "int":
      return Number.parseInt(text, 10);
    case "float":
      return Number.parseFloat(text);
    case "choice":
      return item.choices.find((c) => String(c) === text) ?? text;
    case "str_list":
      return text.split(",").map((s) => s.trim()).filter(Boolean);
    default:
      return text;
  }
}

function toText(value: unknown): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}

function SettingRow({ item }: { item: SettingItem }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(toText(item.value));
  const [bool, setBool] = useState(item.type === "bool" ? Boolean(item.value) : false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<{ version: number; value: unknown; reason: string | null; created_at: string; by: string | null }[] | null>(null);

  const dirty = item.type === "bool" ? bool !== Boolean(item.value) : text !== toText(item.value);

  const save = async () => {
    setError("");
    setSaved(false);
    setBusy(true);
    try {
      const value = item.type === "bool" ? bool : parseInput(item, text);
      await api(`/admin/settings/${item.key}`, { method: "PUT", body: { value, reason: reason || null } });
      await queryClient.invalidateQueries({ queryKey: ["admin-settings"] });
      setReason("");
      setSaved(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-b border-gray-100 py-4 last:border-0">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-[14rem] flex-1">
          <p className="font-medium">{item.label}</p>
          <p className="text-xs text-gray-500">{item.description}</p>
          <p className="mt-1 text-xs text-gray-400">
            <code>{item.key}</code> · {item.version ? `version ${item.version}` : "default"}
            {item.min !== null && ` · min ${item.min}`}
            {item.max !== null && ` · max ${item.max}`}
          </p>
        </div>
        <div className="w-full max-w-xs space-y-2">
          {item.type === "bool" ? (
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={bool} onChange={(e) => setBool(e.target.checked)} /> Enabled
            </label>
          ) : item.type === "choice" ? (
            <select className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={text} onChange={(e) => setText(e.target.value)}>
              {item.choices.map((c) => (
                <option key={String(c)} value={String(c)}>{String(c)}</option>
              ))}
            </select>
          ) : (
            <div className="flex items-center gap-2">
              <input
                aria-label={item.label}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
                value={text}
                onChange={(e) => setText(e.target.value)}
                inputMode={item.type === "int" || item.type === "float" ? "decimal" : undefined}
              />
              {item.unit && <span className="text-sm text-gray-500">{item.unit}</span>}
            </div>
          )}
          {item.type === "str_list" && item.choices.length > 0 && <p className="text-xs text-gray-500">Allowed: {item.choices.join(", ")}</p>}
          {item.type === "str_list" && item.choices.length === 0 && <p className="text-xs text-gray-500">Comma-separated</p>}
          {dirty && (
            <>
              <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" placeholder="Reason for change (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <div className="flex gap-2">
                <Button onClick={save} loading={busy}>Save</Button>
                <Button variant="secondary" onClick={() => { setText(toText(item.value)); setBool(Boolean(item.value)); }}>Reset</Button>
              </div>
            </>
          )}
          {error && <Alert>{error}</Alert>}
          {saved && !dirty && <Alert kind="success">Saved as a new version. Applies to new bookings only.</Alert>}
          <button className="text-xs text-gray-500 hover:underline" onClick={async () => setHistory(history ? null : await api(`/admin/settings/${item.key}/history`))}>
            {history ? "Hide history" : "Show history"}
          </button>
          {history && (
            <ul className="space-y-1 text-xs text-gray-600">
              {history.length === 0 && <li>No changes yet (using default).</li>}
              {history.map((h) => (
                <li key={h.version}>
                  v{h.version}: <strong>{toText(h.value)}</strong> · {h.by ?? "system"} · {new Date(h.created_at).toLocaleString()}
                  {h.reason && ` · "${h.reason}"`}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

export function SettingsPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-settings"], queryFn: () => api<SettingItem[]>("/admin/settings") });
  if (isLoading) return <p className="text-gray-500">Loading settings...</p>;
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  const groups = [...new Set(data!.map((s) => s.group))];
  return (
    <div className="space-y-6">
      <Alert kind="info">Every change creates a new version and is recorded in the audit log. Changes apply to new bookings only; existing bookings keep their stored values.</Alert>
      {groups.map((g) => (
        <Card key={g} title={g}>
          {data!.filter((s) => s.group === g).map((s) => (
            <SettingRow key={`${s.key}-${s.version}`} item={s} />
          ))}
        </Card>
      ))}
    </div>
  );
}

export function AuditPage() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const { data, error } = useQuery({
    queryKey: ["audit", page, action],
    queryFn: () => api<{ total: number; page: number; page_size: number; items: AuditEntry[] }>(`/admin/audit?page=${page}&page_size=25${action ? `&action=${encodeURIComponent(action)}` : ""}`),
  });
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  return (
    <Card title="Audit log">
      <div className="mb-4 max-w-xs">
        <Field id="action" label="Filter by action" placeholder="e.g. setting.updated" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} />
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">When</th><th className="pr-3">Who</th><th className="pr-3">Action</th><th className="pr-3">Target</th><th>Change</th></tr>
          </thead>
          <tbody>
            {data?.items.map((a) => (
              <tr key={a.id} className="border-b border-gray-100 align-top">
                <td className="whitespace-nowrap py-2 pr-3">{new Date(a.created_at).toLocaleString()}</td>
                <td className="pr-3">{a.actor ?? "system"}</td>
                <td className="pr-3"><code>{a.action}</code></td>
                <td className="pr-3">{a.entity_type}{a.entity_id ? `: ${a.entity_id}` : ""}</td>
                <td className="text-xs text-gray-600">
                  {a.before != null && <div>from {JSON.stringify(a.before)}</div>}
                  {a.after != null && <div>to {JSON.stringify(a.after)}</div>}
                  {a.reason && <div>reason: {a.reason}</div>}
                </td>
              </tr>
            ))}
            {data?.items.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-gray-500">No entries.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="mt-4 flex items-center gap-3">
        <Button variant="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
        <span className="text-sm text-gray-600">Page {page} of {pages}</span>
        <Button variant="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
      </div>
    </Card>
  );
}

export function LegalAdminPage() {
  const [docType, setDocType] = useState("terms");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const publish = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ version: number }>(`/admin/legal/${docType}`, { method: "POST", body: { title, body } });
      setMsg({ kind: "success", text: `Published version ${r.version}. Users must accept it at their next visit.` });
      setBody("");
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Publish a legal document">
      <div className="space-y-4">
        <Alert kind="info">Publishing creates a new version. All users are asked to re-accept Terms and Privacy at their next login.</Alert>
        {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        <select className="rounded-lg border border-gray-300 px-3 py-2 text-sm" value={docType} onChange={(e) => setDocType(e.target.value)}>
          <option value="terms">Terms of Service</option>
          <option value="privacy">Privacy Policy</option>
          <option value="host_terms">Host Terms</option>
        </select>
        <Field id="title" label="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <div>
          <label htmlFor="body" className="mb-1 block text-sm font-medium text-gray-700">Full text</label>
          <textarea id="body" rows={12} className="w-full rounded-lg border border-gray-300 p-3 text-sm" value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
        <Button onClick={publish} loading={busy} disabled={!title.trim() || !body.trim()}>Publish new version</Button>
      </div>
    </Card>
  );
}
