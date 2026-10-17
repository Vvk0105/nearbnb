import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { CancellationTemplate, PolicyTier } from "../../lib/types";
import { describeTiers } from "../../components/CancelBooking";
import { Alert, Button, Card, Field, errorMessage } from "../../components/ui";

type Row = { hours: string; pct: string };
const toRows = (tiers: PolicyTier[]): Row[] => tiers.map((t) => ({ hours: String(t.hours_before), pct: String(t.refund_pct) }));
const toTiers = (rows: Row[]): PolicyTier[] => rows.map((r) => ({ hours_before: Number(r.hours), refund_pct: Number(r.pct) }));

function TiersEditor({ rows, onChange, idPrefix }: { rows: Row[]; onChange: (rows: Row[]) => void; idPrefix: string }) {
  return (
    <div className="space-y-2">
      {rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-end gap-2">
          <Field id={`${idPrefix}-h${i}`} label="At least (hours before check-in)" inputMode="numeric" value={r.hours} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, hours: e.target.value } : x)))} />
          <Field id={`${idPrefix}-p${i}`} label="Refund %" inputMode="numeric" value={r.pct} onChange={(e) => onChange(rows.map((x, j) => (j === i ? { ...x, pct: e.target.value } : x)))} />
          <button type="button" className="mb-2 text-sm text-red-700 underline" onClick={() => onChange(rows.filter((_, j) => j !== i))}>Remove step</button>
        </div>
      ))}
      <button type="button" className="text-sm text-brand-700 underline" onClick={() => onChange([...rows, { hours: "", pct: "" }])}>Add a step</button>
    </div>
  );
}

function TemplateCard({ t, onSaved }: { t: CancellationTemplate; onSaved: () => Promise<unknown> }) {
  const [name, setName] = useState(t.name);
  const [description, setDescription] = useState(t.description);
  const [strictness, setStrictness] = useState(String(t.strictness));
  const [rows, setRows] = useState(toRows(t.tiers));
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const save = async (extra: Record<string, unknown> = {}) => {
    setMsg(null);
    try {
      await api(`/admin/cancellation-templates/${t.id}`, { method: "PATCH", body: { name, description, strictness: Number(strictness), tiers: toTiers(rows), ...extra } });
      await onSaved();
      setMsg({ kind: "success", text: "Saved. New bookings use this; existing bookings keep the policy they were made under." });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    }
  };
  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">{t.name} <span className="text-xs font-normal text-gray-500">({t.key}, version {t.version}{t.active ? "" : ", switched off"})</span></h3>
        <button className="text-sm text-brand-700 underline" onClick={() => save({ active: !t.active })}>{t.active ? "Switch off" : "Switch on"}</button>
      </div>
      <ul className="mb-3 list-disc pl-5 text-sm text-gray-600">{describeTiers(t.tiers).map((l) => <li key={l}>{l}</li>)}</ul>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field id={`n-${t.id}`} label="Name" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="sm:col-span-2"><Field id={`d-${t.id}`} label="Description shown to guests" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        <Field id={`s-${t.id}`} label="Strictness (higher is stricter)" inputMode="numeric" value={strictness} onChange={(e) => setStrictness(e.target.value)} hint="Hosts cannot choose a policy stricter than the limit in Settings." />
      </div>
      <div className="mt-3"><TiersEditor idPrefix={`t-${t.id}`} rows={rows} onChange={setRows} /></div>
      <div className="mt-3 space-y-2">
        {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        <Button onClick={() => save()}>Save changes</Button>
      </div>
    </Card>
  );
}

export function AdminCancellationPage() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["admin-templates"], queryFn: () => api<CancellationTemplate[]>("/admin/cancellation-templates") });
  const [form, setForm] = useState({ key: "", name: "", description: "", strictness: "2" });
  const [rows, setRows] = useState<Row[]>([{ hours: "24", pct: "100" }]);
  const [msg, setMsg] = useState("");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["admin-templates"] });
  const add = async () => {
    setMsg("");
    try {
      await api("/admin/cancellation-templates", { method: "POST", body: { ...form, strictness: Number(form.strictness), tiers: toTiers(rows) } });
      setForm({ key: "", name: "", description: "", strictness: "2" });
      setRows([{ hours: "24", pct: "100" }]);
      await refresh();
    } catch (e) {
      setMsg(errorMessage(e));
    }
  };
  return (
    <div className="space-y-6">
      <Alert kind="info">A policy says how much of the accommodation a guest gets back, by how far ahead they cancel. The service fee is not refunded unless you change that in Settings. The default policy and the strictest policy a host may choose are also in Settings.</Alert>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {data?.map((t) => <TemplateCard key={`${t.id}-${t.version}-${t.active}`} t={t} onSaved={refresh} />)}
      <Card title="Add a policy">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field id="nk" label="Key (letters and numbers)" value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} />
          <Field id="nn" label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Field id="ns" label="Strictness" inputMode="numeric" value={form.strictness} onChange={(e) => setForm({ ...form, strictness: e.target.value })} />
          <div className="sm:col-span-3"><Field id="nd" label="Description shown to guests" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
        </div>
        <div className="mt-3"><TiersEditor idPrefix="new" rows={rows} onChange={setRows} /></div>
        {msg && <div className="mt-3"><Alert>{msg}</Alert></div>}
        <div className="mt-3"><Button onClick={add} disabled={!form.key || !form.name}>Add policy</Button></div>
      </Card>
    </div>
  );
}
