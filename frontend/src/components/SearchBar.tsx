import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { timeOptions, todayIso } from "../lib/format";
import type { BookingMeta, Place } from "../lib/types";
import { PlaceInput } from "./PlaceInput";
import { Button } from "./ui";

export type SearchValues = { q: string; lat: string; lng: string; date: string; time: string; nights: string; guests: string };

export const EMPTY_SEARCH: SearchValues = { q: "", lat: "", lng: "", date: "", time: "14:00", nights: "1", guests: "2" };

export function readSearch(params: URLSearchParams): SearchValues {
  return {
    q: params.get("q") ?? "", lat: params.get("lat") ?? "", lng: params.get("lng") ?? "", date: params.get("date") ?? "",
    time: params.get("time") ?? "14:00", nights: params.get("nights") ?? "1", guests: params.get("guests") ?? "2",
  };
}

export function useBookingMeta() {
  return useQuery({ queryKey: ["booking-meta"], queryFn: () => api<BookingMeta>("/meta/booking"), staleTime: 300_000 });
}

/** Place + check-in date/time + nights + guests. Hands the values to `onSearch`; the page decides what to do with them. */
export function SearchBar({ initial, onSearch, onPickProperty, compact = false }: {
  initial: SearchValues; onSearch: (v: SearchValues) => void; compact?: boolean;
  onPickProperty?: (propertyId: string) => void;   // when given, the Where box also suggests property names and opens the one picked
}) {
  const { data: meta } = useBookingMeta();
  const [v, setV] = useState<SearchValues>(initial);
  const times = timeOptions(meta?.interval_minutes ?? 60);
  const maxNights = Math.max(1, Math.floor((meta?.max_stay_hours ?? 168) / (meta?.step_hours ?? 24)));
  const set = (k: keyof SearchValues) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setV({ ...v, [k]: e.target.value });

  const place = (p: Place) => setV((cur) => ({ ...cur, lat: String(p.latitude), lng: String(p.longitude) }));
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onSearch(v);
  };
  // min-w-0 matters on phones: a date box has a built-in minimum width, and without this the grid lets it push out past the card.
  const field = "w-full min-w-0 max-w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500";
  const lab = "mb-1 block text-xs font-semibold uppercase tracking-wide text-gray-500";

  return (
    <form onSubmit={submit} className={`grid gap-3 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm ${compact ? "md:grid-cols-12" : "md:grid-cols-12 md:p-5"}`} role="search">
      <div className="min-w-0 md:col-span-4">
        <span className={lab}>Where</span>
        <PlaceInput id="where" value={v.q} onChange={(text) => setV({ ...v, q: text, lat: "", lng: "" })} onSelect={place} onPickProperty={onPickProperty} placeholder="Suburb, city or postcode" />
      </div>
      <div className="min-w-0 md:col-span-2">
        <label htmlFor="sb-date" className={lab}>Check-in</label>
        <input id="sb-date" type="date" min={todayIso()} value={v.date} onChange={set("date")} className={field} />
      </div>
      <div className="min-w-0 md:col-span-2">
        <label htmlFor="sb-time" className={lab}>Time</label>
        <select id="sb-time" value={v.time} onChange={set("time")} className={field}>
          {times.map((t) => <option key={t} value={t}>{timeText(t)}</option>)}
        </select>
      </div>
      <div className="min-w-0 md:col-span-1">
        <label htmlFor="sb-nights" className={lab}>Nights</label>
        <select id="sb-nights" value={v.nights} onChange={set("nights")} className={field}>
          {Array.from({ length: maxNights }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      <div className="min-w-0 md:col-span-1">
        <label htmlFor="sb-guests" className={lab}>Guests</label>
        <input id="sb-guests" type="number" min={1} max={100} value={v.guests} onChange={set("guests")} className={field} />
      </div>
      <div className="flex items-end md:col-span-2">
        <Button type="submit" className="w-full">Search</Button>
      </div>
    </form>
  );
}

function timeText(t: string): string {
  const [h, m] = t.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

export function searchToParams(v: SearchValues): URLSearchParams {
  const p = new URLSearchParams();
  if (v.q.trim()) p.set("q", v.q.trim());
  if (v.lat && v.lng) { p.set("lat", v.lat); p.set("lng", v.lng); }
  if (v.date) { p.set("date", v.date); p.set("time", v.time); p.set("nights", v.nights); }
  if (v.guests && v.guests !== "1") p.set("guests", v.guests);
  return p;
}
