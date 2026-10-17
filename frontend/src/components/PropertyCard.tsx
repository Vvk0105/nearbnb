import { Link } from "react-router-dom";
import { mediaUrl } from "../lib/api";
import { money } from "../lib/format";
import type { SearchItem } from "../lib/types";

export function PropertyCard({ item, carry }: { item: SearchItem; carry: string }) {
  const image = mediaUrl(item.thumb_url);
  return (
    <Link to={`/property/${item.id}${carry ? `?${carry}` : ""}`} className="group block overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm transition hover:shadow-md">
      <div className="aspect-[4/3] bg-gray-100">
        {image ? <img src={image} alt="" loading="lazy" className="h-full w-full object-cover transition group-hover:scale-[1.02]" />
          : <div className="flex h-full items-center justify-center text-sm text-gray-400">No photo</div>}
      </div>
      <div className="p-4">
        <p className="truncate font-semibold">{item.name}</p>
        <p className="truncate text-sm text-gray-500">
          {[item.type_name, [item.suburb, item.state].filter(Boolean).join(", ")].filter(Boolean).join(" · ")}
          {item.distance_km !== null && ` · ${item.distance_km} km away`}
        </p>
        <p className="mt-2 text-sm"><span className="font-semibold">{money(item.min_price, item.currency)}</span> <span className="text-gray-500">/ 24 hours{item.units.length > 1 ? " · from" : ""}</span></p>
      </div>
    </Link>
  );
}
