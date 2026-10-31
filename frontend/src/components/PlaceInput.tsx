import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Place } from "../lib/types";
import { Icon } from "./icons";

type Suggestion = { place_id: string; text: string; main: string; secondary: string };
export type ListingHints = {
  places: { text: string; suburb: string; state: string; properties: number }[];
  properties: { id: string; name: string; suburb: string; state: string; type_name: string | null }[];
};

type Entry =
  | { kind: "area"; key: string; text: string; main: string; secondary: string }
  | { kind: "google"; key: string; s: Suggestion }
  | { kind: "property"; key: string; id: string; main: string; secondary: string };

const newToken = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));

/**
 * Text box with suggestions as you type.
 *  - Google (through our server) suggests areas and addresses, when a Google key is configured.
 *  - With `onPickProperty`, it also suggests places guests can book right now: property names (opening one goes straight to it)
 *    and the areas those properties are in. That part needs no Google key.
 * With neither available it is a plain text box, so searching by typing a name still works.
 * `onSelect` receives coordinates and a parsed address when a Google place is picked.
 */
export function PlaceInput({
  id, label, value, onChange, onSelect, onPickProperty, kind = "region", placeholder, className = "",
}: {
  id: string; label?: string; value: string; onChange: (text: string) => void; onSelect?: (place: Place) => void;
  onPickProperty?: (propertyId: string) => void;
  kind?: "region" | "address"; placeholder?: string; className?: string;
}) {
  const withListings = Boolean(onPickProperty);
  const { data: status } = useQuery({ queryKey: ["places-status"], queryFn: () => api<{ enabled: boolean }>("/places/status"), staleTime: 600_000 });
  const [open, setOpen] = useState(false);
  const [google, setGoogle] = useState<Suggestion[]>([]);
  const [hints, setHints] = useState<ListingHints | null>(null);
  const [active, setActive] = useState(-1);
  const [error, setError] = useState("");
  const token = useRef(newToken());
  const picked = useRef<string | null>(null);   // the text a pick just put in the box: it should not start another search
  const input = useRef<HTMLInputElement>(null);
  // Suggestions are for someone typing. Text that arrives any other way (the results page rebuilds the bar with the searched place in it) must not pop the list open.
  const typing = () => document.activeElement === input.current;

  useEffect(() => {
    if (!status?.enabled || value === picked.current || value.trim().length < 3 || !typing()) {
      if (value.trim().length < 3) setGoogle([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await api<{ suggestions: Suggestion[] }>(`/places/autocomplete?q=${encodeURIComponent(value.trim())}&kind=${kind}&token=${token.current}`);
        setGoogle(res.suggestions);
        setActive(-1);
        setError("");
        setOpen(true);
      } catch {
        setGoogle([]);
        setError("Suggestions are unavailable. You can still type a place name.");
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [value, status?.enabled, kind]);

  useEffect(() => {
    if (!withListings || value === picked.current || value.trim().length < 2 || !typing()) {
      if (value.trim().length < 2) setHints(null);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        setHints(await api<ListingHints>(`/search/suggest?q=${encodeURIComponent(value.trim())}`));
        setActive(-1);
        setOpen(true);
      } catch {
        setHints(null);                                   // our own suggestions failing is never worth an error message
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [value, withListings]);

  const entries = useMemo<Entry[]>(() => {
    const out: Entry[] = [];
    for (const p of hints?.places ?? []) out.push({ kind: "area", key: `area-${p.text}`, text: p.text, main: p.text, secondary: `${p.properties} place${p.properties === 1 ? "" : "s"} to stay` });
    for (const s of google) out.push({ kind: "google", key: `g-${s.place_id}`, s });
    for (const p of hints?.properties ?? []) out.push({ kind: "property", key: `p-${p.id}`, id: p.id, main: p.name, secondary: [p.type_name, [p.suburb, p.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ") });
    return out;
  }, [hints, google]);

  const close = () => { setOpen(false); setActive(-1); };

  const pickGoogle = async (s: Suggestion) => {
    picked.current = s.text;
    onChange(s.text);
    close();
    setGoogle([]);
    setHints(null);
    try {
      const place = await api<Place>(`/places/details/${encodeURIComponent(s.place_id)}?token=${token.current}`);
      onSelect?.(place);
    } catch {
      setError("Could not load that place. Try another.");
    } finally {
      token.current = newToken(); // a new billing session starts after each pick
    }
  };

  const pick = (e: Entry) => {
    if (e.kind === "google") return void pickGoogle(e.s);
    if (e.kind === "area") {
      picked.current = e.text;
      onChange(e.text);
      setHints(null);
      setGoogle([]);
      return close();
    }
    close();
    onPickProperty?.(e.id);
  };

  const onKey = (ev: React.KeyboardEvent) => {
    if (ev.key === "Enter" && active < 0) close();   // Enter with nothing highlighted is a search: the form submits, the list goes away
    if (!open || entries.length === 0) return;
    if (ev.key === "ArrowDown") { ev.preventDefault(); setActive((a) => (a + 1) % entries.length); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setActive((a) => (a <= 0 ? entries.length - 1 : a - 1)); }
    else if (ev.key === "Enter" && active >= 0) { ev.preventDefault(); pick(entries[active]); }
    else if (ev.key === "Escape") close();
  };

  const row = (e: Entry, i: number) => (
    <li key={e.key} role="option" aria-selected={i === active}
      onMouseDown={(ev) => { ev.preventDefault(); pick(e); }}
      className={`flex cursor-pointer items-center gap-3 px-3 py-2 text-sm ${i === active ? "bg-brand-50" : "hover:bg-gray-50"}`}>
      <Icon name={e.kind === "property" ? "home" : "search"} className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="min-w-0">
        <span className="block truncate font-medium">{e.kind === "google" ? e.s.main : e.main}</span>
        <span className="block truncate text-xs text-gray-500">{e.kind === "google" ? e.s.secondary : e.secondary}</span>
      </span>
    </li>
  );
  const indexed = entries.map((e, i) => ({ e, i }));
  const places = indexed.filter(({ e }) => e.kind !== "property");
  const properties = indexed.filter(({ e }) => e.kind === "property");

  return (
    <div className={`relative min-w-0 ${className}`}>
      {label && <label htmlFor={id} className="mb-1 block text-sm font-medium text-gray-700">{label}</label>}
      <input
        id={id} ref={input} value={value} placeholder={placeholder} autoComplete="off" role="combobox" aria-expanded={open && entries.length > 0} aria-controls={`${id}-list`}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }} onKeyDown={onKey} onBlur={() => setTimeout(close, 150)} onFocus={() => entries.length > 0 && setOpen(true)}
        className="w-full min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
      />
      {open && entries.length > 0 && (
        <ul id={`${id}-list`} role="listbox" className="absolute z-30 mt-1 max-h-80 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          {places.length > 0 && withListings && <li role="presentation" className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Places</li>}
          {places.map(({ e, i }) => row(e, i))}
          {properties.length > 0 && <li role="presentation" className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">Properties</li>}
          {properties.map(({ e, i }) => row(e, i))}
        </ul>
      )}
      {error && <p className="mt-1 text-xs text-gray-500">{error}</p>}
    </div>
  );
}
