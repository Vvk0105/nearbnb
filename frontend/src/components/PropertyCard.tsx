import { Link } from "react-router-dom";
import { mediaUrl } from "../lib/api";
import { money } from "../lib/format";
import type { SearchItem } from "../lib/types";
import { Icon } from "./icons";
import { RatingSummary } from "./Reviews";

export function PropertyCard({ item, carry }: { item: SearchItem; carry: string }) {
  const image = mediaUrl(item.thumb_url);
  return (
    <Link to={`/property/${item.id}${carry ? `?${carry}` : ""}`} className="group block overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg">
      <div className="relative aspect-[4/3] overflow-hidden bg-slate-100">
        {image ? <img src={image} alt="" loading="lazy" className="h-full w-full object-cover transition duration-300 group-hover:scale-105" />
          : <div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-slate-400"><Icon name="home" className="h-8 w-8" />No photo</div>}
        {item.type_name && <span className="absolute left-3 top-3 rounded-full bg-white/90 px-2.5 py-1 text-xs font-medium text-slate-700 shadow-sm backdrop-blur">{item.type_name}</span>}
      </div>
      <div className="p-4">
        <p className="truncate text-base font-semibold">{item.name}</p>
        <p className="mt-0.5 flex items-center gap-1 truncate text-sm text-slate-500">
          <span className="truncate">
            {[item.suburb, item.state].filter(Boolean).join(", ")}
            {item.distance_km !== null && ` · ${item.distance_km} km away`}
          </span>
        </p>
        {item.rating && <p className="mt-1.5"><RatingSummary average={item.rating.average} count={item.rating.count} /></p>}
        <p className="mt-3 border-t border-slate-100 pt-3 text-sm"><span className="text-lg font-semibold">{money(item.min_price, item.currency)}</span> <span className="text-slate-500">/ {item.units.length > 0 && item.units.every((u) => u.stay_mode === "fixed") ? "night" : "24 hours"}{item.units.length > 1 ? " · from" : ""}</span></p>
      </div>
    </Link>
  );
}
