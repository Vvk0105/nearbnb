import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mediaUrl } from "../../lib/api";
import { fieldLabel, money } from "../../lib/format";
import type { PropertyData } from "../../lib/types";
import { Alert, Button, Card, Field, Select, StatusBadge, TextArea, errorMessage } from "../../components/ui";

type Row = {
  id: string; name: string; status: string; suburb: string; state: string; host_email: string; host_name: string;
  units: number; pending_units: number; has_pending_revision: boolean; submitted_at: string | null;
};
type RevisionRow = { id: string; entity_type: string; label: string; property_id: string; fields: string[]; photos_changed: boolean; submitted_at: string | null };
type Revision = { id: string; entity_type: string; entity_id: string; data: Record<string, unknown>; photos_changed: boolean; status: string; note: string | null; submitted_at: string | null };
type Detail = PropertyData & { host: { id: string; name: string; email: string; state: string }; revisions: Revision[] };

export function ListingsPage() {
  const [status, setStatus] = useState("submitted");
  const [q, setQ] = useState("");
  const props = useQuery({ queryKey: ["admin-props", status, q], queryFn: () => api<{ items: Row[] }>(`/admin/properties?page_size=100${status ? `&status=${status}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`) });
  const revs = useQuery({ queryKey: ["admin-revisions"], queryFn: () => api<RevisionRow[]>("/admin/revisions") });
  return (
    <div className="space-y-6">
      <Card title={`Changes waiting for approval (${revs.data?.length ?? 0})`}>
        <p className="mb-3 text-sm text-gray-600">Edits to listings that are already live. Guests keep seeing the old version until you approve.</p>
        <ul className="space-y-2">
          {revs.data?.map((r) => (
            <li key={r.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-sm">
              <span><strong>{r.label}</strong> · {[...r.fields.map(fieldLabel), ...(r.photos_changed ? ["Photos"] : [])].join(", ")}</span>
              <Link className="text-brand-600 hover:underline" to={`/admin/listings/${r.property_id}`}>Review</Link>
            </li>
          ))}
          {revs.data?.length === 0 && <li className="text-sm text-gray-500">Nothing waiting.</li>}
        </ul>
      </Card>
      <Card title="Listings">
        <div className="mb-4 grid gap-3 sm:grid-cols-2">
          <Select id="pstatus" label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="submitted">Waiting for review</option>
            <option value="approved">Live</option>
            <option value="changes_requested">Changes requested</option>
            <option value="rejected">Rejected</option>
            <option value="paused">Paused by host</option>
            <option value="unpublished">Unpublished by admin</option>
            <option value="draft">Drafts</option>
            <option value="">Everything</option>
          </Select>
          <Field id="pq" label="Search property, host or suburb" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        {props.error && <Alert>{errorMessage(props.error)}</Alert>}
        <div className="overflow-x-auto"><table className="data-table">
          <thead className="border-b text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Property</th><th className="pr-3">Host</th><th className="pr-3">Units</th><th className="pr-3">Status</th><th>Submitted</th></tr></thead>
          <tbody>
            {props.data?.items.map((p) => (
              <tr key={p.id} className="border-b border-gray-100">
                <td className="py-2 pr-3"><Link className="text-brand-600 hover:underline" to={`/admin/listings/${p.id}`}>{p.name}</Link><div className="text-xs text-gray-500">{[p.suburb, p.state].filter(Boolean).join(", ")}</div></td>
                <td className="pr-3">{p.host_name}<div className="text-xs text-gray-500">{p.host_email}</div></td>
                <td className="pr-3">{p.units}{p.pending_units > 0 && <span className="ml-1 text-xs text-amber-700">({p.pending_units} pending)</span>}</td>
                <td className="pr-3"><StatusBadge status={p.status} />{p.has_pending_revision && <span className="ml-1"><StatusBadge status="pending" /></span>}</td>
                <td>{p.submitted_at ? new Date(p.submitted_at).toLocaleDateString() : "-"}</td>
              </tr>
            ))}
            {props.data?.items.length === 0 && <tr><td colSpan={5} className="py-6 text-center text-gray-500">No listings here.</td></tr>}
          </tbody>
        </table></div>
      </Card>
    </div>
  );
}

function Decision({ onDecide, busy, approveLabel = "Approve", options = true }: { onDecide: (decision: string, note: string) => void; busy: boolean; approveLabel?: string; options?: boolean }) {
  const [note, setNote] = useState("");
  return (
    <div className="space-y-3">
      <TextArea id={`note-${approveLabel}`} label="Message to the host" rows={2} value={note} onChange={(e) => setNote(e.target.value)} hint="Required unless you approve." />
      <div className="flex flex-wrap gap-3">
        <Button loading={busy} onClick={() => onDecide("approve", note)}>{approveLabel}</Button>
        {options && <Button variant="secondary" loading={busy} onClick={() => onDecide("request_changes", note)}>Request changes</Button>}
        <Button variant="danger" loading={busy} onClick={() => onDecide("reject", note)}>Reject</Button>
      </div>
    </div>
  );
}

export function ListingDetailPage() {
  const { propertyId } = useParams();
  const queryClient = useQueryClient();
  const { data, error } = useQuery({ queryKey: ["admin-prop", propertyId], queryFn: () => api<Detail>(`/admin/properties/${propertyId}`) });
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");

  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return <p className="text-gray-500">Loading...</p>;

  const act = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: "success", text: success });
      await queryClient.invalidateQueries({ queryKey: ["admin-prop", propertyId] });
      void queryClient.invalidateQueries({ queryKey: ["admin-props"] });
      void queryClient.invalidateQueries({ queryKey: ["admin-revisions"] });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const pendingUnits = data.units.filter((u) => u.status === "submitted");
  const pendingRevs = data.revisions.filter((r) => r.status === "pending");
  const entityName = (r: Revision) => (r.entity_type === "property" ? "Property" : `Unit: ${data.units.find((u) => u.id === r.entity_id)?.name ?? ""}`);

  return (
    <div className="space-y-6">
      <div>
        <Link to="/admin/listings" className="text-sm text-brand-600 hover:underline">← Listings</Link>
        <div className="mt-1 flex items-center gap-3"><h1 className="text-2xl font-bold">{data.name}</h1><StatusBadge status={data.status} /></div>
        <p className="text-sm text-gray-600">Host: <Link className="text-brand-600 hover:underline" to={`/admin/hosts/${data.host.id}`}>{data.host.name}</Link> ({data.host.email}) · <StatusBadge status={data.host.state} /></p>
      </div>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}

      {data.status === "submitted" && (
        <Card title="Decision on this listing">
          <p className="mb-3 text-sm text-gray-600">Applies to the property and all of its submitted units.</p>
          <Decision busy={busy} onDecide={(decision, note) => act(() => api(`/admin/properties/${propertyId}/decision`, { method: "POST", body: { decision, note } }), "Decision recorded and the host was emailed.")} />
        </Card>
      )}

      {pendingRevs.length > 0 && (
        <Card title="Changes waiting for approval">
          <div className="space-y-6">
            {pendingRevs.map((r) => (
              <div key={r.id} className="rounded-lg border border-amber-200 bg-amber-50 p-4">
                <p className="mb-2 font-medium">{entityName(r)}</p>
                <ul className="mb-3 space-y-1 text-sm">
                  {Object.entries(r.data).map(([k, v]) => <li key={k}><strong>{fieldLabel(k)}:</strong> <span className="whitespace-pre-wrap">{typeof v === "number" && k.includes("price") ? money(v) : String(v)}</span></li>)}
                  {r.photos_changed && <li><strong>Photos</strong> were added or removed (see the photos below, marked as pending).</li>}
                </ul>
                <Decision busy={busy} approveLabel="Approve change" options={false} onDecide={(decision, note) => act(() => api(`/admin/revisions/${r.id}/decision`, { method: "POST", body: { decision, note } }), `Change ${decision === "approve" ? "approved" : "rejected"}.`)} />
              </div>
            ))}
          </div>
        </Card>
      )}

      {pendingUnits.length > 0 && data.status !== "submitted" && (
        <Card title="Units waiting for approval">
          {pendingUnits.map((u) => (
            <div key={u.id} className="mb-4"><p className="mb-2 font-medium">{u.name}</p>
              <Decision busy={busy} onDecide={(decision, note) => act(() => api(`/admin/units/${u.id}/decision`, { method: "POST", body: { decision, note } }), "Decision recorded.")} /></div>
          ))}
        </Card>
      )}

      <Card title="Property">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-gray-500">Type</dt><dd>{data.type_name ?? "-"}</dd></div>
          <div><dt className="text-xs text-gray-500">Timezone</dt><dd>{data.timezone}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Address</dt><dd>{[data.address_line1, data.address_line2, data.suburb, data.state, data.postcode, data.country].filter(Boolean).join(", ")}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Description</dt><dd className="whitespace-pre-wrap">{data.description || "-"}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">House rules</dt><dd className="whitespace-pre-wrap">{data.rules || "-"}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Check-in instructions (private)</dt><dd className="whitespace-pre-wrap">{data.checkin_instructions || "-"}</dd></div>
        </dl>
        <div className="mt-4 grid grid-cols-3 gap-2 sm:grid-cols-5">
          {data.photos.map((p) => <div key={p.id} className="relative"><img src={mediaUrl(p.thumb_url)} alt="" className={`h-20 w-full rounded object-cover ${p.status === "pending_delete" ? "opacity-40" : ""}`} />{p.status !== "approved" && <div className="absolute left-1 top-1"><StatusBadge status={p.status} /></div>}</div>)}
        </div>
      </Card>

      <Card title="Units">
        <div className="space-y-4">
          {data.units.map((u) => (
            <div key={u.id} className="rounded-lg border border-gray-200 p-4">
              <div className="flex items-center justify-between"><p className="font-medium">{u.name}</p><StatusBadge status={u.status} /></div>
              <p className="mt-1 text-sm text-gray-600">{money(u.price_per_24h, u.currency)} / 24h · cleaning {money(u.cleaning_fee, u.currency)} · sleeps {u.capacity} · {u.quantity} room(s) · check-in {u.checkin_from}–{u.checkin_to}</p>
              <p className="mt-1 text-sm text-gray-600">{u.description}</p>
              <p className="mt-1 text-xs text-gray-500">Amenities: {u.amenities.map((a) => a.name).join(", ") || "none"}</p>
              <div className="mt-3 grid grid-cols-4 gap-2 sm:grid-cols-6">
                {u.photos.map((p) => <div key={p.id} className="relative"><img src={mediaUrl(p.thumb_url)} alt="" className={`h-16 w-full rounded object-cover ${p.status === "pending_delete" ? "opacity-40" : ""}`} />{p.status !== "approved" && <div className="absolute left-1 top-1"><StatusBadge status={p.status} /></div>}</div>)}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {(data.status === "approved" || data.status === "paused") && (
        <Card title="Take offline">
          <div className="space-y-3">
            <Field id="unpub" label="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button variant="danger" loading={busy} onClick={() => act(() => api(`/admin/properties/${propertyId}/unpublish`, { method: "POST", body: { reason } }), "Listing unpublished.")}>Unpublish</Button>
          </div>
        </Card>
      )}
      {data.status === "unpublished" && (
        <Card title="Unpublished"><Button loading={busy} onClick={() => act(() => api(`/admin/properties/${propertyId}/republish`, { method: "POST" }), "Listing is live again.")}>Republish</Button></Card>
      )}

      {data.revisions.filter((r) => r.status !== "pending").length > 0 && (
        <Card title="Past changes">
          <ul className="space-y-1 text-sm">
            {data.revisions.filter((r) => r.status !== "pending").map((r) => (
              <li key={r.id}>{entityName(r)}: {Object.keys(r.data).map(fieldLabel).join(", ") || "photos"} → <StatusBadge status={r.status} />{r.note ? ` · ${r.note}` : ""}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

type Item = { id: string; name: string; active: boolean };

function CatalogueList({ title, kind, items }: { title: string; kind: "property-types" | "amenities"; items: Item[] }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      await queryClient.invalidateQueries({ queryKey: ["catalogues"] });
    } catch (e) {
      setError(errorMessage(e));
    }
  };
  return (
    <Card title={title}>
      {error && <Alert>{error}</Alert>}
      <ul className="my-3 divide-y divide-gray-100">
        {items.map((i) => (
          <li key={i.id} className="flex items-center justify-between py-2 text-sm">
            <span className={i.active ? "" : "text-gray-400 line-through"}>{i.name}</span>
            <button className="text-xs text-brand-600 hover:underline" onClick={() => run(() => api(`/admin/${kind}/${i.id}`, { method: "PUT", body: { name: i.name, active: !i.active } }))}>{i.active ? "Hide from hosts" : "Show again"}</button>
          </li>
        ))}
      </ul>
      <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void run(async () => { await api(`/admin/${kind}`, { method: "POST", body: { name } }); setName(""); }); }}>
        <div className="flex-1"><Field id={`new-${kind}`} label="Add new" value={name} onChange={(e) => setName(e.target.value)} required /></div>
        <Button type="submit">Add</Button>
      </form>
    </Card>
  );
}

export function CataloguesPage() {
  const { data, error } = useQuery({ queryKey: ["catalogues"], queryFn: () => api<{ property_types: Item[]; amenities: Item[] }>("/admin/catalogues") });
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  if (!data) return <p className="text-gray-500">Loading...</p>;
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <CatalogueList title="Property types" kind="property-types" items={data.property_types} />
      <CatalogueList title="Amenities" kind="amenities" items={data.amenities} />
    </div>
  );
}
