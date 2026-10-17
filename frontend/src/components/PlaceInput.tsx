import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Place } from "../lib/types";

type Suggestion = { place_id: string; text: string; main: string; secondary: string };

const newToken = () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2));

/** Text box with place suggestions from Google (through our server). When no Google key is configured it behaves like
 *  a plain text box, so searching by name still works. `onSelect` receives coordinates and a parsed address. */
export function PlaceInput({
  id, label, value, onChange, onSelect, kind = "region", placeholder, className = "",
}: {
  id: string; label?: string; value: string; onChange: (text: string) => void; onSelect?: (place: Place) => void;
  kind?: "region" | "address"; placeholder?: string; className?: string;
}) {
  const { data: status } = useQuery({ queryKey: ["places-status"], queryFn: () => api<{ enabled: boolean }>("/places/status"), staleTime: 600_000 });
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Suggestion[]>([]);
  const [active, setActive] = useState(-1);
  const [error, setError] = useState("");
  const token = useRef(newToken());
  const skipNext = useRef(false);

  useEffect(() => {
    if (!status?.enabled || skipNext.current || value.trim().length < 3) {
      skipNext.current = false;
      if (value.trim().length < 3) setItems([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await api<{ suggestions: Suggestion[] }>(`/places/autocomplete?q=${encodeURIComponent(value.trim())}&kind=${kind}&token=${token.current}`);
        setItems(res.suggestions);
        setActive(-1);
        setError("");
        setOpen(true);
      } catch {
        setItems([]);
        setError("Suggestions are unavailable. You can still type a place name.");
      }
    }, 250);
    return () => clearTimeout(timer);
  }, [value, status?.enabled, kind]);

  const pick = async (s: Suggestion) => {
    skipNext.current = true;
    onChange(s.text);
    setOpen(false);
    setItems([]);
    try {
      const place = await api<Place>(`/places/details/${encodeURIComponent(s.place_id)}?token=${token.current}`);
      onSelect?.(place);
    } catch {
      setError("Could not load that place. Try another.");
    } finally {
      token.current = newToken(); // a new billing session starts after each pick
    }
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (!open || items.length === 0) return;
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => (a + 1) % items.length); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => (a <= 0 ? items.length - 1 : a - 1)); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); void pick(items[active]); }
    else if (e.key === "Escape") setOpen(false);
  };

  return (
    <div className={`relative ${className}`}>
      {label && <label htmlFor={id} className="mb-1 block text-sm font-medium text-gray-700">{label}</label>}
      <input
        id={id} value={value} placeholder={placeholder} autoComplete="off" role="combobox" aria-expanded={open} aria-controls={`${id}-list`}
        onChange={(e) => { onChange(e.target.value); setOpen(true); }} onKeyDown={onKey} onBlur={() => setTimeout(() => setOpen(false), 150)} onFocus={() => items.length > 0 && setOpen(true)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
      />
      {open && items.length > 0 && (
        <ul id={`${id}-list`} role="listbox" className="absolute z-30 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-gray-200 bg-white py-1 shadow-lg">
          {items.map((s, i) => (
            <li key={s.place_id} role="option" aria-selected={i === active}
              onMouseDown={(e) => { e.preventDefault(); void pick(s); }}
              className={`cursor-pointer px-3 py-2 text-sm ${i === active ? "bg-brand-50" : "hover:bg-gray-50"}`}>
              <span className="font-medium">{s.main}</span> <span className="text-gray-500">{s.secondary}</span>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-1 text-xs text-gray-500">{error}</p>}
    </div>
  );
}
