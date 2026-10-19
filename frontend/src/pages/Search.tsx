import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { dayLabel, timeLabel } from "../lib/format";
import type { ListingMeta, SearchResult } from "../lib/types";
import { PropertyCard } from "../components/PropertyCard";
import { SearchBar, readSearch, searchToParams, type SearchValues } from "../components/SearchBar";
import { Alert, Button, Card, Field, Select, errorMessage } from "../components/ui";
import { EmptyState } from "../components/dashboardUi";

const PAGE_SIZE = 12;

export function apiQueryFrom(p: URLSearchParams): string {
  const v = readSearch(p);
  const out = new URLSearchParams();
  if (v.q) out.set("q", v.q);
  if (v.lat && v.lng) { out.set("lat", v.lat); out.set("lng", v.lng); }
  if (v.date) { out.set("checkin", `${v.date}T${v.time}`); out.set("hours", String(Number(v.nights) * 24)); }
  if (v.guests) out.set("guests", v.guests);
  const min = p.get("min"), max = p.get("max");
  if (min) out.set("min_price", String(Math.round(Number(min) * 100)));
  if (max) out.set("max_price", String(Math.round(Number(max) * 100)));
  (p.get("types") ?? "").split(",").filter(Boolean).forEach((t) => out.append("type_ids", t));
  (p.get("amenities") ?? "").split(",").filter(Boolean).forEach((a) => out.append("amenity_ids", a));
  out.set("sort", p.get("sort") ?? "relevance");
  out.set("page", p.get("page") ?? "1");
  out.set("page_size", String(PAGE_SIZE));
  return out.toString();
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const values = readSearch(params);
  const apiQuery = apiQueryFrom(params);
  const { data, error, isFetching } = useQuery({ queryKey: ["search", apiQuery], queryFn: () => api<SearchResult>(`/search?${apiQuery}`), placeholderData: keepPreviousData });
  const { data: meta } = useQuery({ queryKey: ["listing-meta"], queryFn: () => api<ListingMeta>("/meta/listing"), staleTime: 300_000 });
  const [min, setMin] = useState(params.get("min") ?? "");
  const [max, setMax] = useState(params.get("max") ?? "");

  const update = (changes: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(changes)) (v === null || v === "" ? next.delete(k) : next.set(k, v));
    if (!("page" in changes)) next.delete("page");
    setParams(next);
  };
  const toggle = (key: "types" | "amenities", id: string) => {
    const set = new Set((params.get(key) ?? "").split(",").filter(Boolean));
    set.has(id) ? set.delete(id) : set.add(id);
    update({ [key]: [...set].join(",") });
  };
  const onSearch = (v: SearchValues) => {
    const next = searchToParams(v);
    for (const k of ["min", "max", "types", "amenities", "sort"]) if (params.get(k)) next.set(k, params.get(k)!);
    setParams(next);
  };

  const carry = new URLSearchParams();
  for (const k of ["date", "time", "nights", "guests"]) if (params.get(k)) carry.set(k, params.get(k)!);
  const page = Number(params.get("page") ?? "1");
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const selected = (key: string) => new Set((params.get(key) ?? "").split(",").filter(Boolean));
  const filtersOn = ["min", "max", "types", "amenities"].some((k) => params.get(k));

  return (
    <div className="space-y-6">
      <SearchBar key={params.toString().replace(/&?(page|sort)=[^&]*/g, "")} initial={values} onSearch={onSearch} compact />
      <div className="grid gap-6 lg:grid-cols-[16rem_1fr]">
        <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
          <Card title="Filters">
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2">
                <Field id="f-min" label="Min $/24h" inputMode="numeric" value={min} onChange={(e) => setMin(e.target.value)} onBlur={() => update({ min })} />
                <Field id="f-max" label="Max $/24h" inputMode="numeric" value={max} onChange={(e) => setMax(e.target.value)} onBlur={() => update({ max })} />
              </div>
              <fieldset>
                <legend className="mb-1 text-sm font-medium text-gray-700">Property type</legend>
                {meta?.property_types.map((t) => (
                  <label key={t.id} className="flex items-center gap-2 py-0.5 text-sm"><input type="checkbox" checked={selected("types").has(t.id)} onChange={() => toggle("types", t.id)} /> {t.name}</label>
                ))}
              </fieldset>
              <fieldset>
                <legend className="mb-1 text-sm font-medium text-gray-700">Amenities</legend>
                {meta?.amenities.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 py-0.5 text-sm"><input type="checkbox" checked={selected("amenities").has(a.id)} onChange={() => toggle("amenities", a.id)} /> {a.name}</label>
                ))}
              </fieldset>
              {filtersOn && <Button variant="secondary" onClick={() => { setMin(""); setMax(""); update({ min: null, max: null, types: null, amenities: null }); }}>Clear filters</Button>}
            </div>
          </Card>
        </aside>

        <section aria-busy={isFetching}>
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-bold tracking-tight">{data ? `${data.total} place${data.total === 1 ? "" : "s"}` : "Searching..."}{values.q && ` for “${values.q}”`}</h1>
              {values.date && <p className="text-sm text-gray-600">Check-in {dayLabel(values.date)}, {timeLabel(values.time)} · {Number(values.nights) * 24} hours · {values.guests} guest{values.guests === "1" ? "" : "s"}</p>}
            </div>
            <div className="w-48">
              <Select id="sort" label="Sort by" value={params.get("sort") ?? "relevance"} onChange={(e) => update({ sort: e.target.value === "relevance" ? null : e.target.value })}>
                <option value="relevance">{values.lat ? "Nearest" : "Best match"}</option>
                <option value="price_asc">Price: low to high</option>
                <option value="price_desc">Price: high to low</option>
                <option value="newest">Newest</option>
              </Select>
            </div>
          </div>

          {error && <Alert>{errorMessage(error)}</Alert>}
          {data?.hint && data.total === 0 && <Alert kind="info">{data.hint}</Alert>}
          {data && data.total === 0 && !data.hint && (
            <EmptyState icon="search" title="Nothing matches yet">Try different dates, fewer filters, or a wider area.</EmptyState>
          )}
          {!data && !error && (
            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3" aria-hidden="true">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="animate-pulse overflow-hidden rounded-2xl border border-slate-200 bg-white">
                  <div className="aspect-[4/3] bg-slate-200" />
                  <div className="space-y-2 p-4"><div className="h-4 w-2/3 rounded bg-slate-200" /><div className="h-3 w-1/2 rounded bg-slate-100" /><div className="h-4 w-1/3 rounded bg-slate-200" /></div>
                </div>
              ))}
            </div>
          )}
          <div className={`grid gap-5 sm:grid-cols-2 xl:grid-cols-3 ${isFetching ? "opacity-70" : ""}`}>
            {data?.items.map((item) => <PropertyCard key={item.id} item={item} carry={carry.toString()} />)}
          </div>
          {pages > 1 && (
            <div className="mt-6 flex items-center justify-center gap-3">
              <Button variant="secondary" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>Previous</Button>
              <span className="text-sm text-gray-600">Page {page} of {pages}</span>
              <Button variant="secondary" disabled={page >= pages} onClick={() => update({ page: String(page + 1) })}>Next</Button>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
