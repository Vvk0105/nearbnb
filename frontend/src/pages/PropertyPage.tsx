import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, mediaUrl } from "../lib/api";
import { money } from "../lib/format";
import type { PublicProperty, PublicUnit } from "../lib/types";
import { AvailabilityPanel } from "../components/availability/AvailabilityPanel";
import { describeTiers } from "../components/CancelBooking";
import { PropertyReviews, RatingSummary } from "../components/Reviews";
import { Alert, Card } from "../components/ui";
import { Gallery } from "../components/Gallery";
import { Icon } from "../components/icons";

function UnitCard({ unit, timezone, open, onToggle, initial }: { unit: PublicUnit; timezone: string; open: boolean; onToggle: () => void; initial: { date?: string; time?: string; nights?: number; guests?: number } }) {
  const facts = [`Sleeps ${unit.capacity}`, unit.bedrooms != null && `${unit.bedrooms} bedroom${unit.bedrooms === 1 ? "" : "s"}`,
    unit.bathrooms != null && `${unit.bathrooms} bathroom${unit.bathrooms === 1 ? "" : "s"}`, unit.beds != null && `${unit.beds} bed${unit.beds === 1 ? "" : "s"}`].filter(Boolean);
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-semibold">{unit.name}</h3>
          <p className="text-sm text-gray-500">{facts.join(" · ")}{unit.quantity > 1 ? ` · ${unit.quantity} rooms of this type` : ""}</p>
          {unit.description && <p className="mt-2 whitespace-pre-wrap text-sm text-gray-700">{unit.description}</p>}
          {unit.amenities.length > 0 && (
            <ul className="mt-3 flex flex-wrap gap-2">{unit.amenities.map((a) => <li key={a.id} className="rounded-full bg-brand-50 px-2.5 py-0.5 text-xs text-brand-800">{a.name}</li>)}</ul>
          )}
        </div>
        <div className="text-right">
          <p className="text-xl font-bold">{money(unit.price_per_24h, unit.currency)}</p>
          <p className="text-xs text-gray-500">per 24 hours{unit.cleaning_fee > 0 ? ` + ${money(unit.cleaning_fee, unit.currency)} cleaning` : ""}</p>
          <button type="button" onClick={onToggle} aria-expanded={open} className="mt-3 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            {open ? "Hide availability" : "Check availability"}
          </button>
        </div>
      </div>
      {unit.photos.length > 0 && (
        <div className="mt-4 flex gap-2 overflow-x-auto">{unit.photos.map((p) => <img key={p.id} src={mediaUrl(p.thumb_url)} alt="" className="h-24 w-36 shrink-0 rounded-lg object-cover" />)}</div>
      )}
      {open && <div className="mt-6 border-t border-gray-100 pt-6"><AvailabilityPanel unit={unit} timezone={timezone} initial={initial} /></div>}
    </Card>
  );
}

export default function PropertyPage() {
  const { propertyId } = useParams();
  const [params] = useSearchParams();
  const { data, error, isLoading } = useQuery({ queryKey: ["public-property", propertyId], queryFn: () => api<PublicProperty>(`/properties/${propertyId}`), retry: false });
  const initial = {
    date: params.get("date") ?? undefined, time: params.get("time") ?? undefined,
    nights: params.get("nights") ? Number(params.get("nights")) : undefined, guests: params.get("guests") ? Number(params.get("guests")) : undefined,
  };
  const [opened, setOpened] = useState<string | null>(null);

  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <div className="mx-auto max-w-xl space-y-4 py-10 text-center">
        <h1 className="text-xl font-bold">{missing ? "This place isn't available" : "Something went wrong"}</h1>
        <p className="text-gray-600">{missing ? "It may have been removed or paused by the host." : "Please try again."}</p>
        <Link to="/search" className="inline-block text-brand-700 hover:underline">Search other places</Link>
      </div>
    );
  }
  if (!data) return null;
  const minPrice = data.units.length ? Math.min(...data.units.map((u) => u.price_per_24h)) : 0;
  const autoOpen = opened ?? (data.units.length === 1 || initial.date ? data.units[0].id : null);

  return (
    <div className="space-y-8">
      <div>
        <Link to="/search" className="text-sm text-brand-700 hover:underline">← Search</Link>
        <h1 className="mt-2 text-3xl font-extrabold tracking-tight md:text-4xl">{data.name}</h1>
        {data.rating && <p className="mt-1"><RatingSummary average={data.rating.average} count={data.rating.count} /></p>}
        <p className="text-gray-600">{[data.type_name, [data.suburb, data.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ")} · Hosted by {data.host_first_name}</p>
      </div>
      <Gallery photos={data.photos} name={data.name} />
      <div className="grid gap-8 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {data.description && <section><h2 className="mb-2 text-lg font-semibold">About this place</h2><p className="whitespace-pre-wrap text-gray-700">{data.description}</p></section>}
          {data.rules && <section><h2 className="mb-2 text-lg font-semibold">House rules</h2><p className="whitespace-pre-wrap text-gray-700">{data.rules}</p></section>}
          <PropertyReviews propertyId={data.id} />
          {data.cancellation && (
            <section aria-label="Cancellation policy">
              <h2 className="mb-2 text-lg font-semibold">Cancellation policy: {data.cancellation.name}</h2>
              <ul className="list-disc space-y-0.5 pl-5 text-sm text-gray-700">{describeTiers(data.cancellation.tiers).map((l) => <li key={l}>{l}</li>)}</ul>
              <p className="mt-1 text-xs text-gray-500">The service fee is not refundable. The policy that applies is saved with your booking.</p>
            </section>
          )}
        </div>
        <aside className="lg:sticky lg:top-24 lg:self-start">
          <div className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <p className="text-sm text-slate-500">{data.units.length > 1 ? "From" : "Price"}</p>
            <p className="-mt-3"><span className="text-3xl font-bold">{money(minPrice, data.units[0]?.currency ?? "AUD")}</span> <span className="text-slate-500">/ 24 hours</span></p>
            <a href="#availability" className="block rounded-lg bg-brand-600 px-4 py-2.5 text-center text-sm font-semibold text-white hover:bg-brand-700">Check availability</a>
            <ul className="space-y-2 border-t border-slate-100 pt-4 text-sm text-slate-600">
              <li className="flex gap-2"><Icon name="host" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> Your host: {data.host_first_name}</li>
              <li className="flex gap-2"><Icon name="calendar" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> Check in at the hour you choose</li>
              <li className="flex gap-2"><Icon name="shield" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> You pay only after the host accepts</li>
            </ul>
            <Alert kind="info">The exact address and check-in instructions are shared after your booking is confirmed.</Alert>
          </div>
        </aside>
      </div>
      <section id="availability" className="scroll-mt-24 space-y-4">
        <h2 className="text-xl font-bold">{data.units.length > 1 ? "Choose where to stay" : "Availability and price"}</h2>
        {data.units.map((u) => (
          <UnitCard key={u.id} unit={u} timezone={data.timezone} open={autoOpen === u.id} onToggle={() => setOpened(autoOpen === u.id ? "" : u.id)} initial={initial} />
        ))}
      </section>
    </div>
  );
}
