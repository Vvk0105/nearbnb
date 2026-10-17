import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Alert, Button, Card, Field, errorMessage } from "../../components/ui";

type Rule = { id: string; country: string; region: string; name: string; rate_percent: number; inclusive: boolean; applies_to: string[]; active: boolean };
const COMPONENTS: [string, string][] = [["accommodation", "Accommodation"], ["cleaning", "Cleaning fee"], ["service_fee", "Service fee"]];

function RuleRow({ rule }: { rule: Rule }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ name: rule.name, rate: String(rule.rate_percent), inclusive: rule.inclusive, applies_to: rule.applies_to, active: rule.active });
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const dirty = form.name !== rule.name || Number(form.rate) !== rule.rate_percent || form.inclusive !== rule.inclusive || form.active !== rule.active
    || [...form.applies_to].sort().join() !== [...rule.applies_to].sort().join();

  const save = async () => {
    setMsg(null);
    try {
      await api(`/admin/tax-rules/${rule.id}`, { method: "PUT", body: { name: form.name, rate_percent: Number(form.rate), inclusive: form.inclusive, applies_to: form.applies_to, active: form.active } });
      await queryClient.invalidateQueries({ queryKey: ["tax-rules"] });
      setMsg({ kind: "success", text: "Saved. New quotes use this rule immediately; existing bookings keep their stored amounts." });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    }
  };

  return (
    <div className="border-b border-gray-100 py-5 last:border-0">
      <p className="mb-3 font-medium">{rule.country}{rule.region && ` · ${rule.region}`}</p>
      <div className="grid gap-4 sm:grid-cols-3">
        <Field id={`n-${rule.id}`} label="Tax name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Field id={`r-${rule.id}`} label="Rate (%)" inputMode="decimal" value={form.rate} onChange={(e) => setForm({ ...form, rate: e.target.value })} />
        <div className="space-y-1 pt-6 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.inclusive} onChange={(e) => setForm({ ...form, inclusive: e.target.checked })} /> Listed prices already include this tax</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} /> Active</label>
        </div>
      </div>
      <fieldset className="mt-3 text-sm">
        <legend className="mb-1 text-gray-600">Tax applies to</legend>
        <div className="flex flex-wrap gap-4">
          {COMPONENTS.map(([key, text]) => (
            <label key={key} className="flex items-center gap-2">
              <input type="checkbox" checked={form.applies_to.includes(key)}
                onChange={(e) => setForm({ ...form, applies_to: e.target.checked ? [...form.applies_to, key] : form.applies_to.filter((x) => x !== key) })} /> {text}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-3 space-y-2">
        {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
        {dirty && <Button onClick={save}>Save changes</Button>}
      </div>
    </div>
  );
}

export function TaxRulesPage() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["tax-rules"], queryFn: () => api<Rule[]>("/admin/tax-rules") });
  const [country, setCountry] = useState("");
  const [name, setName] = useState("");
  const [rate, setRate] = useState("");
  const [msg, setMsg] = useState("");

  const add = async () => {
    setMsg("");
    try {
      await api("/admin/tax-rules", { method: "POST", body: { country, name, rate_percent: Number(rate) } });
      setCountry(""); setName(""); setRate("");
      await queryClient.invalidateQueries({ queryKey: ["tax-rules"] });
    } catch (e) {
      setMsg(errorMessage(e));
    }
  };

  return (
    <div className="space-y-6">
      <Alert kind="info">Tax is calculated on every quote from these rules. Confirm rates and who is responsible for the tax with your accountant before launch.</Alert>
      {error && <Alert>{errorMessage(error)}</Alert>}
      <Card title="Tax rules">{data?.map((r) => <RuleRow key={`${r.id}-${r.rate_percent}-${r.active}`} rule={r} />)}</Card>
      <Card title="Add a country">
        <div className="grid gap-4 sm:grid-cols-4">
          <Field id="tc" label="Country (2 letters)" maxLength={2} value={country} onChange={(e) => setCountry(e.target.value)} />
          <Field id="tn" label="Tax name" value={name} onChange={(e) => setName(e.target.value)} />
          <Field id="tr" label="Rate (%)" inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
          <div className="pt-6"><Button onClick={add} disabled={!country || !name || !rate}>Add</Button></div>
        </div>
        {msg && <div className="mt-3"><Alert>{msg}</Alert></div>}
      </Card>
    </div>
  );
}
