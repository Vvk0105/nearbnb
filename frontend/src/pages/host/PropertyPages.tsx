import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mediaUrl } from "../../lib/api";
import { fieldLabel, fromCents, money, toCents } from "../../lib/format";
import type { CalendarData, CancellationTemplate, ListingMeta, PropertyData, UnitData } from "../../lib/types";
import { CalendarSyncCard } from "../../components/CalendarSync";
import { describeTiers } from "../../components/CancelBooking";
import { PhotoManager } from "../../components/PhotoManager";
import { PlaceInput } from "../../components/PlaceInput";
import { Alert, Button, Card, Field, Select, StatusBadge, TeamNote, TextArea, errorMessage } from "../../components/ui";
import { EmptyState } from "../../components/dashboardUi";
import { Icon } from "../../components/icons";
import { useHost } from "./HostPages";

const TIMEZONES = ["Australia/Sydney", "Australia/Melbourne", "Australia/Brisbane", "Australia/Adelaide", "Australia/Perth", "Australia/Hobart", "Australia/Darwin", "UTC"];

function useMeta() {
  return useQuery({ queryKey: ["listing-meta"], queryFn: () => api<ListingMeta>("/meta/listing"), staleTime: 300_000 });
}

function CancellationChoice({ value, onChange, disabled }: { value: string; onChange: (v: string) => void; disabled?: boolean }) {
  const { data } = useQuery({ queryKey: ["cancellation-meta"], queryFn: () => api<CancellationTemplate[]>("/meta/cancellation"), staleTime: 300_000 });
  const chosen = data?.find((t) => t.key === value);
  return (
    <div>
      <label htmlFor="cancellation_template_key" className="mb-1 block text-sm font-medium text-gray-700">Cancellation policy</label>
      <select id="cancellation_template_key" disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-gray-50">
        <option value="">Use the platform default</option>
        {data?.map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
      </select>
      {chosen && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-gray-600">{describeTiers(chosen.tiers).map((l) => <li key={l}>{l}</li>)}</ul>}
      <p className="mt-1 text-xs text-gray-500">Shown to guests on your listing. Each booking keeps the policy it was made under, so changing this only affects new bookings.</p>
    </div>
  );
}

function useProperty(id: string | undefined) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["property", id], queryFn: () => api<PropertyData>(`/host/properties/${id}`), enabled: Boolean(id) });
  const setProperty = (data: PropertyData) => queryClient.setQueryData(["property", id], data);
  return { property: query.data, loading: query.isLoading, error: query.error, setProperty };
}

function useAction() {
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string | void>) => {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      const message = await fn();
      if (message) setInfo(message);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  // The answer to a button press is shown next to the button, and scrolled into view if the button was far down the page.
  const anchor = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error || info) anchor.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [error, info]);
  const banner = (error || info) ? (
    <div ref={anchor} className="space-y-2">
      {error && <Alert>{error}</Alert>}
      {info && <Alert kind="success">{info}</Alert>}
    </div>
  ) : null;
  return { run, busy, banner };
}

export function PropertiesPage() {
  const { host } = useHost();
  const navigate = useNavigate();
  const { data, isLoading, error } = useQuery({ queryKey: ["properties"], queryFn: () => api<PropertyData[]>("/host/properties") });
  const [name, setName] = useState("");
  const { run, busy, banner } = useAction();

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const created = await api<PropertyData>("/host/properties", { method: "POST", body: { name } });
      navigate(`/host/properties/${created.id}`);
    });
  };

  if (error) return <Alert>{errorMessage(error)}</Alert>;
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Your properties</h1>
      {host && !host.can_create_listings && <Alert kind="info">You can add properties once your host application is approved.</Alert>}
      {host?.can_create_listings && !host.can_submit_listings && <Alert kind="info">You can draft properties now. Submitting for approval unlocks when payout setup is complete.</Alert>}
      {host?.can_create_listings && (
        <Card title="Add a property">
          <form onSubmit={create} className="flex items-end gap-3">
            <div className="flex-1"><Field id="newname" label="Property name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Bondi Beach House" /></div>
            <Button type="submit" loading={busy}>Create</Button>
          </form>
          <div className="mt-3">{banner}</div>
        </Card>
      )}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        {data?.map((p) => {
          const cover = p.photos?.[0]?.thumb_url;
          const from = p.units.length ? Math.min(...p.units.map((u) => u.price_per_24h)) : null;
          return (
            <Link key={p.id} to={`/host/properties/${p.id}`} className="group flex overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:border-brand-300 hover:shadow-md">
              {cover ? <img src={mediaUrl(cover)} alt="" className="h-auto w-28 shrink-0 object-cover sm:w-36" /> : <div className="flex w-28 shrink-0 items-center justify-center bg-slate-100 text-slate-300 sm:w-36"><Icon name="home" className="h-8 w-8" /></div>}
              <div className="min-w-0 flex-1 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="truncate font-semibold">{p.name}</p>
                  {p.pending_revisions && p.pending_revisions.length > 0 && <StatusBadge status="pending" />}
                  <StatusBadge status={p.status} />
                </div>
                <p className="mt-1 truncate text-sm text-slate-500">{[p.suburb, p.state].filter(Boolean).join(", ") || "No address yet"}</p>
                <p className="mt-2 text-xs text-slate-500">{p.units.length} unit(s){from !== null ? ` · from ${money(from, p.units[0].currency)} / 24h` : ""}</p>
              </div>
            </Link>
          );
        })}
        {data?.length === 0 && <div className="sm:col-span-2"><EmptyState icon="home" title="No properties yet">Add your first one above to get started.</EmptyState></div>}
      </div>
    </div>
  );
}

export function PropertyEditor() {
  const { propertyId } = useParams();
  const { property, loading, error, setProperty } = useProperty(propertyId);
  const { host } = useHost();
  const { data: meta } = useMeta();
  const [form, setForm] = useState<Record<string, string>>({});
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [finder, setFinder] = useState("");
  const [newUnit, setNewUnit] = useState({ name: "", price: "", capacity: "2", quantity: "1" });
  const details = useAction();
  const life = useAction();
  const units = useAction();
  const queryClient = useQueryClient();

  useEffect(() => {
    if (property && loadedFor !== property.id) {
      setLoadedFor(property.id);
      setForm({
        name: property.name, description: property.description, rules: property.rules, type_id: property.type_id ?? "",
        address_line1: property.address_line1, address_line2: property.address_line2, suburb: property.suburb, state: property.state,
        postcode: property.postcode, country: property.country, timezone: property.timezone,
        latitude: property.latitude?.toString() ?? "", longitude: property.longitude?.toString() ?? "",
        checkin_instructions: property.checkin_instructions ?? "",
        cancellation_template_key: property.cancellation_template_key ?? "",
      });
    }
  }, [property, loadedFor]);

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error || !property) return <Alert>{errorMessage(error ?? new Error("Property not found"))}</Alert>;

  const locked = property.status === "submitted" || property.status === "archived";
  const live = ["approved", "paused", "unpublished"].includes(property.status);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });
  const refreshList = () => queryClient.invalidateQueries({ queryKey: ["properties"] });

  const save = (e: FormEvent) => {
    e.preventDefault();
    void details.run(async () => {
      const body: Record<string, unknown> = { ...form, type_id: form.type_id || undefined };
      body.latitude = form.latitude ? Number(form.latitude) : undefined;
      body.longitude = form.longitude ? Number(form.longitude) : undefined;
      for (const k of Object.keys(body)) if (body[k] === undefined) delete body[k];
      const updated = await api<PropertyData>(`/host/properties/${property.id}`, { method: "PATCH", body });
      setProperty(updated);
      void refreshList();
      const waiting = updated.result?.pending_review ?? [];
      return waiting.length ? `Saved. These changes need admin approval before they go live: ${waiting.map(fieldLabel).join(", ")}.` : "Saved.";
    });
  };

  const lifecycle = (action: "submit" | "pause" | "resume" | "archive") =>
    life.run(async () => {
      setProperty(await api<PropertyData>(`/host/properties/${property.id}/${action}`, { method: "POST" }));
      void refreshList();
      return action === "submit" ? "Submitted for review. You will get an email with the decision." : undefined;
    });

  const addUnit = (e: FormEvent) => {
    e.preventDefault();
    void units.run(async () => {
      const updated = await api<PropertyData>(`/host/properties/${property.id}/units`, {
        method: "POST", body: { name: newUnit.name, price_per_24h: toCents(newUnit.price), capacity: Number(newUnit.capacity), quantity: Number(newUnit.quantity) },
      });
      setProperty(updated);
      setNewUnit({ name: "", price: "", capacity: "2", quantity: "1" });
    });
  };

  const canSubmit = host?.can_submit_listings && ["draft", "changes_requested", "rejected"].includes(property.status);
  const checklist = property.checklist ?? [];

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link to="/host/properties" className="text-sm text-brand-600 hover:underline">← All properties</Link>
        <div className="mt-1 flex items-center gap-3"><h1 className="text-2xl font-bold">{property.name}</h1><StatusBadge status={property.status} /></div>
      </div>

      <TeamNote status={property.status} note={property.review_note} />
      {property.status === "submitted" && <Alert kind="info">This listing is being reviewed, so it can't be edited until we decide.</Alert>}
      {property.status === "unpublished" && <Alert>This listing was taken offline by NearBNB. Contact support if you think this is a mistake.</Alert>}
      {property.pending_revisions && property.pending_revisions.length > 0 && (
        <Alert kind="info">
          Waiting for approval, still showing the old version to guests:{" "}
          {property.pending_revisions.map((r) => `${r.entity_type === "unit" ? "unit: " : ""}${[...r.fields.map(fieldLabel), ...(r.photos_changed ? ["Photos"] : [])].join(", ")}`).join(" · ")}
        </Alert>
      )}

      {!live && property.status !== "submitted" && (
        <Card title="Before you submit">
          {checklist.length === 0 ? <p className="text-sm text-green-700">Everything is ready.</p> : (
            <ul className="list-disc space-y-1 pl-5 text-sm text-gray-700">{checklist.map((c, i) => <li key={i}>{c.message}</li>)}</ul>
          )}
          {!host?.can_submit_listings && <p className="mt-3 text-sm text-gray-500">Submitting unlocks when your payout setup is complete.</p>}
          <div className="mt-4">{life.banner}
            <div className="mt-2"><Button onClick={() => lifecycle("submit")} loading={life.busy} disabled={!canSubmit || checklist.length > 0}>Submit for approval</Button></div>
          </div>
        </Card>
      )}
      {live && (
        <Card title="Listing status">
          {life.banner}
          <div className="mt-2 flex flex-wrap gap-3">
            {property.status === "approved" && <Button variant="secondary" onClick={() => lifecycle("pause")} loading={life.busy}>Pause listing</Button>}
            {property.status === "paused" && <Button onClick={() => lifecycle("resume")} loading={life.busy}>Resume listing</Button>}
            <Button variant="danger" onClick={() => window.confirm("Archive this property? It will be removed from your list.") && lifecycle("archive")}>Archive</Button>
          </div>
        </Card>
      )}

      <Card title="Details">
        <form onSubmit={save} className="space-y-4">
          <Field id="name" label="Property name" value={form.name ?? ""} onChange={set("name")} disabled={locked} required />
          <Select id="type_id" label="Property type" value={form.type_id ?? ""} onChange={set("type_id")} disabled={locked}>
            <option value="">Choose...</option>
            {meta?.property_types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select>
          <TextArea id="description" label="Description" value={form.description ?? ""} onChange={set("description")} disabled={locked} />
          <TextArea id="rules" label="House rules" rows={3} value={form.rules ?? ""} onChange={set("rules")} disabled={locked} />
          {!locked && (
            <PlaceInput id="find_address" kind="address" label="Find the address" placeholder="Start typing the street address"
              value={finder} onChange={setFinder}
              onSelect={(p) => setForm((f) => ({ ...f, ...p.address, address_line1: p.address.address_line1 || f.address_line1, latitude: String(p.latitude), longitude: String(p.longitude) }))} />
          )}
          <Field id="address_line1" label="Street address" value={form.address_line1 ?? ""} onChange={set("address_line1")} disabled={locked} />
          <Field id="address_line2" label="Address line 2" value={form.address_line2 ?? ""} onChange={set("address_line2")} disabled={locked} />
          <div className="grid grid-cols-2 gap-4">
            <Field id="suburb" label="Suburb / city" value={form.suburb ?? ""} onChange={set("suburb")} disabled={locked} />
            <Field id="state" label="State" value={form.state ?? ""} onChange={set("state")} disabled={locked} />
            <Field id="postcode" label="Postcode" value={form.postcode ?? ""} onChange={set("postcode")} disabled={locked} />
            <Field id="country" label="Country (2 letters)" maxLength={2} value={form.country ?? ""} onChange={set("country")} disabled={locked} />
          </div>
          <div>
            <Field id="timezone" label="Timezone" list="tz-list" value={form.timezone ?? ""} onChange={set("timezone")} disabled={locked} hint="All check-in times for this property use this timezone. It is set from the state automatically; change it only if it's wrong." />
            <datalist id="tz-list">{TIMEZONES.map((t) => <option key={t} value={t} />)}</datalist>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <Field id="latitude" label="Latitude (optional)" inputMode="decimal" value={form.latitude ?? ""} onChange={set("latitude")} disabled={locked} />
            <Field id="longitude" label="Longitude (optional)" inputMode="decimal" value={form.longitude ?? ""} onChange={set("longitude")} disabled={locked} />
          </div>
          <CancellationChoice value={form.cancellation_template_key ?? ""} onChange={(v) => setForm({ ...form, cancellation_template_key: v })} disabled={locked} />
          <TextArea id="checkin_instructions" label="Check-in instructions" rows={3} value={form.checkin_instructions ?? ""} onChange={set("checkin_instructions")} disabled={locked} hint="Door codes, parking, how to get in. Guests only see this after their booking is confirmed." />
          {details.banner}
          {!locked && <Button type="submit" loading={details.busy}>Save details</Button>}
        </form>
      </Card>

      <Card title="Property photos">
        <PhotoManager<PropertyData> base={`/host/properties/${property.id}`} photos={property.photos} disabled={locked} onChange={setProperty} />
      </Card>

      <Card title="Bookable units">
        <p className="mb-3 text-sm text-gray-600">A unit is what a guest actually books: a whole house, or a room type such as "Standard room" (with a number of identical rooms).</p>
        <div className="space-y-3">
          {property.units.map((u) => (
            <Link key={u.id} to={`/host/properties/${property.id}/units/${u.id}`} className="flex items-center justify-between rounded-lg border border-gray-200 p-3 hover:border-brand-300">
              <div>
                <p className="font-medium">{u.name}</p>
                <p className="text-xs text-gray-500">{u.price_per_24h ? `${money(u.price_per_24h, u.currency)} / 24h` : "No price yet"} · sleeps {u.capacity} · {u.quantity} room(s)</p>
              </div>
              <StatusBadge status={u.status} />
            </Link>
          ))}
          {property.units.length === 0 && <p className="text-sm text-gray-500">No units yet.</p>}
        </div>
        {property.status !== "submitted" && (
          <form onSubmit={addUnit} className="mt-5 grid grid-cols-2 gap-3 border-t border-gray-100 pt-4 sm:grid-cols-5">
            <div className="col-span-2"><Field id="unit_name" label="Unit name" required value={newUnit.name} onChange={(e) => setNewUnit({ ...newUnit, name: e.target.value })} placeholder="Entire house" /></div>
            <Field id="unit_price" label="Price / 24h ($)" required inputMode="decimal" value={newUnit.price} onChange={(e) => setNewUnit({ ...newUnit, price: e.target.value })} />
            <Field id="unit_capacity" label="Guests" type="number" min={1} value={newUnit.capacity} onChange={(e) => setNewUnit({ ...newUnit, capacity: e.target.value })} />
            <Field id="unit_quantity" label="Rooms" type="number" min={1} value={newUnit.quantity} onChange={(e) => setNewUnit({ ...newUnit, quantity: e.target.value })} />
            <div className="col-span-full flex items-center gap-3"><Button type="submit" loading={units.busy}>Add unit</Button>{units.banner}</div>
          </form>
        )}
      </Card>
    </div>
  );
}

function BlockCalendar({ unit, timezone, disabled }: { unit: UnitData; timezone: string; disabled: boolean }) {
  const queryClient = useQueryClient();
  const from = new Date().toISOString().slice(0, 10);
  const to = new Date(Date.now() + 180 * 86_400_000).toISOString().slice(0, 10);
  const key = ["calendar", unit.id];
  const { data } = useQuery({ queryKey: key, queryFn: () => api<CalendarData>(`/host/units/${unit.id}/calendar?from=${from}&to=${to}`) });
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [room, setRoom] = useState(unit.rooms.length > 1 ? "all" : "");
  const [note, setNote] = useState("");
  const { run, busy, banner } = useAction();
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });

  const add = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      await api(`/host/units/${unit.id}/blocks`, {
        method: "POST",
        body: { start_local: start, end_local: end, note: note || undefined, all_rooms: room === "all", room_id: room && room !== "all" ? room : undefined },
      });
      setStart(""); setEnd(""); setNote("");
      await refresh();
      return "Period blocked.";
    });
  };

  const kindLabel: Record<string, string> = { host_block: "Blocked by you", booking: "Booked", hold: "Booking request", external_block: "Imported calendar" };
  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-600">Times are in the property's timezone ({timezone}). Block a period when the place isn't available (maintenance, personal use). Back-to-back blocks are fine. You can't block time that already has a booking.</p>
      {banner}
      {!disabled && (
        <form onSubmit={add} className="grid gap-3 sm:grid-cols-2">
          <Field id="block_start" label="From" type="datetime-local" step={3600} required value={start} onChange={(e) => setStart(e.target.value)} />
          <Field id="block_end" label="Until" type="datetime-local" step={3600} required value={end} onChange={(e) => setEnd(e.target.value)} />
          {unit.rooms.length > 1 && (
            <Select id="block_room" label="Room" value={room} onChange={(e) => setRoom(e.target.value)}>
              <option value="all">All rooms</option>
              {unit.rooms.map((r) => <option key={r.id} value={r.id}>Room {r.label}</option>)}
            </Select>
          )}
          <Field id="block_note" label="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="sm:col-span-2"><Button type="submit" loading={busy}>Block this period</Button></div>
        </form>
      )}
      <div className="overflow-x-auto">
        <div className="overflow-x-auto"><table className="data-table">
          <thead className="border-b text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">From</th><th className="pr-3">Until</th>{unit.rooms.length > 1 && <th className="pr-3">Room</th>}<th className="pr-3">Type</th><th /></tr></thead>
          <tbody>
            {data?.entries.map((en) => (
              <tr key={en.id} className="border-b border-gray-100">
                <td className="py-2 pr-3">{en.start_local.replace("T", " ")}</td>
                <td className="pr-3">{en.end_local.replace("T", " ")}</td>
                {unit.rooms.length > 1 && <td className="pr-3">{en.room_label}</td>}
                <td className="pr-3">{kindLabel[en.kind] ?? en.kind}{en.note ? ` · ${en.note}` : ""}</td>
                <td>{en.kind === "host_block" && !disabled && <button className="text-xs text-red-600 hover:underline" onClick={() => run(async () => { await api(`/host/units/${unit.id}/blocks/${en.id}`, { method: "DELETE" }); await refresh(); })}>Remove</button>}</td>
              </tr>
            ))}
            {data?.entries.length === 0 && <tr><td colSpan={5} className="py-4 text-center text-gray-500">Nothing blocked or booked in the next 6 months.</td></tr>}
          </tbody>
        </table></div>
      </div>
    </div>
  );
}

export function UnitEditor() {
  const { propertyId, unitId } = useParams();
  const { property, loading, error, setProperty } = useProperty(propertyId);
  const { host } = useHost();
  const { data: meta } = useMeta();
  const navigate = useNavigate();
  const [form, setForm] = useState<Record<string, string>>({});
  const [amenities, setAmenities] = useState<string[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const save = useAction();
  const life = useAction();
  const rooms = useAction();
  const unit = property?.units.find((u) => u.id === unitId);

  useEffect(() => {
    if (unit && loadedFor !== unit.id) {
      setLoadedFor(unit.id);
      setForm({
        name: unit.name, description: unit.description, price: fromCents(unit.price_per_24h), cleaning: fromCents(unit.cleaning_fee),
        capacity: String(unit.capacity), quantity: String(unit.quantity), bedrooms: unit.bedrooms?.toString() ?? "",
        bathrooms: unit.bathrooms?.toString() ?? "", beds: unit.beds?.toString() ?? "", max_stay: unit.max_stay_hours?.toString() ?? "",
        checkin_from: unit.checkin_from, checkin_to: unit.checkin_to,
        stay_mode: unit.stay_mode ?? "flexible", fixed_checkin_time: unit.fixed_checkin_time ?? "12:00", fixed_checkout_time: unit.fixed_checkout_time ?? "11:00",
      });
      setAmenities(unit.amenities.map((a) => a.id));
    }
    if (unit) setLabels(Object.fromEntries(unit.rooms.map((r) => [r.id, r.label])));
  }, [unit, loadedFor]);

  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (error || !property) return <Alert>{errorMessage(error ?? new Error("Not found"))}</Alert>;
  if (!unit) return <Alert>Unit not found.</Alert>;

  const locked = unit.status === "submitted" || unit.status === "archived" || property.status === "archived";
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm({ ...form, [k]: e.target.value });
  const num = (v: string) => (v === "" ? undefined : Number(v));
  const fixed = form.stay_mode === "fixed";

  const submitForm = (e: FormEvent) => {
    e.preventDefault();
    void save.run(async () => {
      const body: Record<string, unknown> = {
        name: form.name, description: form.description, price_per_24h: toCents(form.price), cleaning_fee: toCents(form.cleaning),
        capacity: Number(form.capacity), quantity: Number(form.quantity), bedrooms: num(form.bedrooms), bathrooms: num(form.bathrooms),
        beds: num(form.beds), max_stay_hours: num(form.max_stay), checkin_from: form.checkin_from, checkin_to: form.checkin_to, amenity_ids: amenities,
        stay_mode: form.stay_mode,
        ...(form.stay_mode === "fixed" ? { fixed_checkin_time: form.fixed_checkin_time, fixed_checkout_time: form.fixed_checkout_time } : {}),
      };
      for (const k of Object.keys(body)) if (body[k] === undefined) delete body[k];
      const updated = await api<PropertyData>(`/host/units/${unit.id}`, { method: "PATCH", body });
      setProperty(updated);
      const waiting = updated.result?.pending_review ?? [];
      return waiting.length ? `Saved. These changes need admin approval before they go live: ${waiting.map(fieldLabel).join(", ")}.` : "Saved.";
    });
  };

  const action = (name: "submit" | "pause" | "resume" | "archive" | "duplicate") =>
    life.run(async () => {
      const updated = await api<PropertyData>(`/host/units/${unit.id}/${name}`, { method: "POST" });
      setProperty(updated);
      if (name === "archive") navigate(`/host/properties/${property.id}`);
      if (name === "duplicate") return "Unit duplicated. Find the copy on the property page.";
      return name === "submit" ? "Submitted for review." : undefined;
    });

  const renameRoom = (roomId: string) =>
    rooms.run(async () => {
      setProperty(await api<PropertyData>(`/host/units/${unit.id}/rooms/${roomId}`, { method: "PATCH", body: { label: labels[roomId] } }));
      return "Room renamed.";
    });

  const propertyLive = ["approved", "paused", "unpublished"].includes(property.status);
  const canSubmitUnit = host?.can_submit_listings && propertyLive && ["draft", "changes_requested", "rejected"].includes(unit.status);
  const unitMissing = (property.checklist ?? []).filter((c) => c.scope === `unit:${unit.id}`);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <Link to={`/host/properties/${property.id}`} className="text-sm text-brand-600 hover:underline">← {property.name}</Link>
        <div className="mt-1 flex items-center gap-3"><h1 className="text-2xl font-bold">{unit.name}</h1><StatusBadge status={unit.status} /></div>
      </div>
      <TeamNote status={unit.status} note={unit.review_note} />
      {unit.status === "submitted" && <Alert kind="info">This unit is being reviewed and can't be edited until we decide.</Alert>}

      <Card title="Price and details">
        <form onSubmit={submitForm} className="space-y-4">
          <Field id="u_name" label="Unit name" value={form.name ?? ""} onChange={set("name")} disabled={locked} required />
          <TextArea id="u_desc" label="Description" rows={3} value={form.description ?? ""} onChange={set("description")} disabled={locked} />
          <div className="grid grid-cols-2 gap-4">
            <Field id="u_price" label={fixed ? "Price per night ($)" : "Price per 24 hours ($)"} inputMode="decimal" value={form.price ?? ""} onChange={set("price")} disabled={locked} hint={fixed ? "One flat price per night. A 2-night stay costs twice this." : "One flat price. A 48-hour stay costs twice this."} />
            <Field id="u_cleaning" label="Cleaning fee per booking ($)" inputMode="decimal" value={form.cleaning ?? ""} onChange={set("cleaning")} disabled={locked} />
            <Field id="u_capacity" label="Guests" type="number" min={1} value={form.capacity ?? ""} onChange={set("capacity")} disabled={locked} />
            <Field id="u_quantity" label="Number of identical rooms" type="number" min={1} value={form.quantity ?? ""} onChange={set("quantity")} disabled={locked} hint="1 for a house. For a hotel room type, how many such rooms you have." />
            <Field id="u_bedrooms" label="Bedrooms" type="number" min={0} value={form.bedrooms ?? ""} onChange={set("bedrooms")} disabled={locked} />
            <Field id="u_bathrooms" label="Bathrooms" type="number" min={0} value={form.bathrooms ?? ""} onChange={set("bathrooms")} disabled={locked} />
            <Field id="u_beds" label="Beds" type="number" min={0} value={form.beds ?? ""} onChange={set("beds")} disabled={locked} />
            <Field id="u_maxstay" label="Max stay (hours, optional)" type="number" min={24} step={24} value={form.max_stay ?? ""} onChange={set("max_stay")} disabled={locked} hint="Leave empty to use the platform maximum." />
          </div>
          <fieldset className="space-y-3 rounded-xl border border-slate-200 p-4">
            <legend className="px-1 text-sm font-medium text-slate-700">Check-in and check-out times</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              {([["flexible", "Flexible", "Guests choose any check-in hour in the window you set, and stay in whole 24-hour blocks. Check-out is the same time of day."],
                ["fixed", "Fixed", "You set one check-in time and one check-out time (for example in 12:00, out 11:30). Guests choose only the date and the number of nights."]] as const).map(([value, title, text]) => (
                <label key={value} className={`flex cursor-pointer gap-3 rounded-xl border p-3 text-sm ${form.stay_mode === value ? "border-brand-500 bg-brand-50" : "border-slate-200"} ${locked ? "cursor-not-allowed opacity-60" : ""}`}>
                  <input type="radio" name="stay_mode" value={value} checked={form.stay_mode === value} disabled={locked} onChange={() => setForm({ ...form, stay_mode: value })} className="mt-1" />
                  <span><span className="block font-medium">{title}</span><span className="block text-xs text-slate-600">{text}</span></span>
                </label>
              ))}
            </div>
            {fixed ? (
              <div className="grid grid-cols-2 gap-4">
                <Field id="u_fixed_in" label="Check-in time" type="time" value={form.fixed_checkin_time ?? ""} onChange={set("fixed_checkin_time")} disabled={locked} required />
                <Field id="u_fixed_out" label="Check-out time" type="time" value={form.fixed_checkout_time ?? ""} onChange={set("fixed_checkout_time")} disabled={locked} required />
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4">
                <Field id="u_from" label="Earliest check-in time" type="time" value={form.checkin_from ?? ""} onChange={set("checkin_from")} disabled={locked} />
                <Field id="u_to" label="Latest check-in time" type="time" value={form.checkin_to ?? ""} onChange={set("checkin_to")} disabled={locked} />
              </div>
            )}
            <p className="text-xs text-slate-500">You can change this only while the unit has no upcoming bookings or held requests.</p>
          </fieldset>
          <fieldset>
            <legend className="mb-2 text-sm font-medium text-gray-700">Amenities</legend>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
              {meta?.amenities.map((a) => (
                <label key={a.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" disabled={locked} checked={amenities.includes(a.id)} onChange={(e) => setAmenities(e.target.checked ? [...amenities, a.id] : amenities.filter((x) => x !== a.id))} /> {a.name}
                </label>
              ))}
            </div>
          </fieldset>
          {save.banner}
          {!locked && <Button type="submit" loading={save.busy}>Save unit</Button>}
        </form>
      </Card>

      <Card title="Photos">
        <PhotoManager<PropertyData> base={`/host/units/${unit.id}`} photos={unit.photos} disabled={locked} onChange={setProperty} />
      </Card>

      {unit.rooms.length > 1 && (
        <Card title="Rooms">
          <p className="mb-3 text-sm text-gray-600">Name your rooms (for example 101, 102) so you know which room a guest was given. Guests never choose a room number.</p>
          {rooms.banner}
          <div className="mt-2 space-y-2">
            {unit.rooms.map((r) => (
              <div key={r.id} className="flex items-end gap-2">
                <div className="flex-1"><Field id={`room_${r.id}`} label="Room name" value={labels[r.id] ?? ""} onChange={(e) => setLabels({ ...labels, [r.id]: e.target.value })} disabled={locked} /></div>
                {!locked && labels[r.id] !== r.label && <Button variant="secondary" onClick={() => renameRoom(r.id)}>Save</Button>}
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Availability">
        <BlockCalendar unit={unit} timezone={property.timezone} disabled={unit.status === "archived"} />
      </Card>

      {unit.quantity === 1 && <CalendarSyncCard unitId={unit.id} disabled={unit.status === "archived"} />}

      <Card title="Listing status">
        {life.banner}
        {propertyLive && unit.status !== "approved" && unit.status !== "paused" && unit.status !== "submitted" && (
          <div className="mb-3">
            {unitMissing.length > 0 && <ul className="mb-2 list-disc pl-5 text-sm text-gray-700">{unitMissing.map((c, i) => <li key={i}>{c.message}</li>)}</ul>}
            <Button onClick={() => action("submit")} disabled={!canSubmitUnit || unitMissing.length > 0}>Submit this unit for approval</Button>
          </div>
        )}
        {!propertyLive && <p className="mb-3 text-sm text-gray-500">This unit is submitted together with its property.</p>}
        <div className="flex flex-wrap gap-3">
          {unit.status === "approved" && <Button variant="secondary" onClick={() => action("pause")}>Pause unit</Button>}
          {unit.status === "paused" && <Button onClick={() => action("resume")}>Resume unit</Button>}
          <Button variant="secondary" onClick={() => action("duplicate")}>Duplicate</Button>
          <Button variant="danger" onClick={() => window.confirm("Archive this unit?") && action("archive")}>Archive</Button>
        </div>
      </Card>
    </div>
  );
}
