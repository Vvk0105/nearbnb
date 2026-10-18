import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl } from "../lib/api";
import type { IcalConflictRow, IcalSourceRow, IcalUnitState } from "../lib/types";
import { Alert, Button, Card, Field, errorMessage } from "./ui";

const ago = (iso: string | null) => {
  if (!iso) return "never";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return minutes < 1 ? "just now" : minutes < 60 ? `${minutes} min ago` : minutes < 1440 ? `${Math.round(minutes / 60)} h ago` : `${Math.round(minutes / 1440)} days ago`;
};

export function ConflictList({ conflicts, onDismiss }: { conflicts: IcalConflictRow[]; onDismiss: (id: string) => Promise<void> }) {
  if (conflicts.length === 0) return null;
  return (
    <ul className="space-y-3" aria-label="Calendar conflicts">
      {conflicts.map((c) => (
        <li key={c.id} className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
          <p className="font-medium text-red-900">{c.source} shows {c.clash_from} to {c.clash_to} as taken, but you have a NearBnB booking on those dates</p>
          <p className="mt-1 text-gray-700">Booking <span className="font-mono">{c.reference}</span> ({c.booking_status.replace(/_/g, " ")}), {c.booking_from} to {c.booking_to}. Nothing was cancelled. Decide which booking to keep and contact the other side if needed.</p>
          <button type="button" className="mt-2 text-red-900 underline" onClick={() => onDismiss(c.id)}>I have dealt with this</button>
        </li>
      ))}
    </ul>
  );
}

function AddSource({ unitId, onAdded }: { unitId: string; onAdded: () => Promise<unknown> }) {
  const [form, setForm] = useState({ name: "", url: "", checkin_time: "15:00", checkout_time: "11:00" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNote("");
    try {
      const r = await api<{ sync: { ok: boolean; error?: string; blocks?: number; conflicts_opened?: number } | null }>(`/host/units/${unitId}/ical/sources`, { method: "POST", body: form });
      setForm({ name: "", url: "", checkin_time: "15:00", checkout_time: "11:00" });
      if (r.sync && !r.sync.ok) setError(`Added, but the first read failed: ${r.sync.error}`);
      else setNote(`Connected. ${r.sync?.blocks ?? 0} booked period${r.sync?.blocks === 1 ? "" : "s"} found.`);
      await onAdded();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-gray-200 p-4">
      <h4 className="font-medium">Connect another site's calendar</h4>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="ical-name" label="Name" placeholder="Airbnb" value={form.name} onChange={set("name")} required />
        <Field id="ical-url" label="Calendar link (iCal / .ics)" placeholder="https://..." value={form.url} onChange={set("url")} required hint="In Airbnb: Calendar > Availability > Connect calendars > Export. In Booking.com: Rates and availability > Calendar sync." />
        <Field id="ical-in" label="Check-in time on that site" type="time" step={900} value={form.checkin_time} onChange={set("checkin_time")} hint="Other sites only know whole days, so we block from this time." />
        <Field id="ical-out" label="Check-out time on that site" type="time" step={900} value={form.checkout_time} onChange={set("checkout_time")} />
      </div>
      {error && <Alert>{error}</Alert>}
      {note && <Alert kind="success">{note}</Alert>}
      <Button type="submit" loading={busy} disabled={!form.name.trim() || !form.url.trim()}>Connect calendar</Button>
    </form>
  );
}

/** Keeps NearBnB and other booking sites from selling the same dates: import their calendar, share ours. */
export function CalendarSync({ unitId, disabled }: { unitId: string; disabled?: boolean }) {
  const queryClient = useQueryClient();
  const key = ["ical", unitId];
  const { data, error, isLoading } = useQuery({ queryKey: key, queryFn: () => api<IcalUnitState>(`/host/units/${unitId}/ical`), refetchInterval: 60_000 });
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: key });

  if (isLoading) return <p className="text-sm text-gray-500">Loading...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  if (!data.eligible) return <Alert kind="info">{data.reason}</Alert>;

  const act = async (fn: () => Promise<unknown>, success?: string) => {
    setMsg(null);
    try {
      await fn();
      if (success) setMsg({ kind: "success", text: success });
      await refresh();
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    }
  };
  const feedUrl = data.export_path ? apiUrl(data.export_path) : "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(feedUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setMsg({ kind: "error", text: "Could not copy. Select the link and copy it by hand." });
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-600">If this place is also listed on Airbnb or Booking.com, connect the calendars so the same dates are never sold twice. Both directions run by themselves: we read their calendar every few minutes, and they read ours.</p>
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <ConflictList conflicts={data.conflicts} onDismiss={(id) => act(() => api(`/host/ical/conflicts/${id}/dismiss`, { method: "POST", body: {} }), "Marked as dealt with.")} />

      <section aria-label="Share your calendar" className="space-y-2">
        <h4 className="font-medium">1. Give the other site our calendar</h4>
        <p className="text-sm text-gray-600">Paste this private link into the other site (Airbnb: Import calendar; Booking.com: Import calendar). It lists your confirmed NearBnB bookings and your own blocks as "Not available", rounded out to whole days. It never contains guest details.</p>
        <div className="flex gap-2">
          <input readOnly aria-label="Your private calendar link" value={feedUrl} onFocus={(e) => e.target.select()} className="w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs" />
          <Button variant="secondary" onClick={copy}>{copied ? "Copied" : "Copy"}</Button>
        </div>
        <button type="button" disabled={disabled} className="text-sm text-gray-600 underline" onClick={() => { if (window.confirm("Make a new link? The old one stops working, so you will need to paste the new one into the other site.")) void act(() => api(`/host/units/${unitId}/ical/export/rotate`, { method: "POST" }), "New link created. Paste it into the other site."); }}>
          Replace this link with a new one
        </button>
      </section>

      <section aria-label="Connected calendars" className="space-y-3">
        <h4 className="font-medium">2. Read the other site's calendar</h4>
        {data.sources.length === 0 && <p className="text-sm text-gray-500">No calendars connected yet.</p>}
        <ul className="space-y-3">
          {data.sources.map((s: IcalSourceRow) => (
            <li key={s.id} className="rounded-lg border border-gray-200 p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-medium">{s.name} <span className="ml-2 text-xs font-normal text-gray-500">{s.active ? "" : "(paused)"}</span></p>
                <p className="text-xs text-gray-500">Last read {ago(s.last_synced_at)} · {s.events_count} booked period{s.events_count === 1 ? "" : "s"} blocked here</p>
              </div>
              {s.failing && s.last_error && <p className="mt-1 text-red-800">Problem: {s.last_error.replace(/\.$/, "")}. The dates already blocked stay blocked.</p>}
              <p className="mt-1 text-xs text-gray-500">Whole days are blocked from {s.checkin_time} to {s.checkout_time}.</p>
              <div className="mt-2 flex flex-wrap gap-3">
                <button type="button" disabled={disabled} className="text-brand-700 underline" onClick={() => act(() => api(`/host/ical/sources/${s.id}/sync`, { method: "POST" }), `${s.name} synced.`)}>Sync now</button>
                <button type="button" disabled={disabled} className="text-gray-700 underline" onClick={() => act(() => api(`/host/ical/sources/${s.id}`, { method: "PATCH", body: { active: !s.active } }))}>{s.active ? "Pause" : "Resume"}</button>
                <button type="button" disabled={disabled} className="text-red-700 underline" onClick={() => { if (window.confirm(`Disconnect ${s.name}? Its blocks are removed from your calendar.`)) void act(() => api(`/host/ical/sources/${s.id}`, { method: "DELETE" }), `${s.name} disconnected.`); }}>Disconnect</button>
              </div>
            </li>
          ))}
        </ul>
        {data.sources.length < data.max_sources && !disabled && <AddSource unitId={unitId} onAdded={refresh} />}
        {data.sources.length >= data.max_sources && <p className="text-xs text-gray-500">You can connect up to {data.max_sources} calendars to one unit.</p>}
      </section>
    </div>
  );
}

export function CalendarSyncCard({ unitId, disabled }: { unitId: string; disabled?: boolean }) {
  return <Card title="Calendar sync (Airbnb, Booking.com...)"><CalendarSync unitId={unitId} disabled={disabled} /></Card>;
}
