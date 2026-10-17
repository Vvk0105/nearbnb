import { dayLabel } from "../../lib/format";
import type { Timeline } from "../../lib/types";

const minutesOf = (local: string) => Number(local.slice(11, 13)) * 60 + Number(local.slice(14, 16));

/** Where an occupied range falls inside the selected day, as percentages of 24 hours.
 *  Ranges that began on an earlier day or end on a later day run to the edge of the bar. */
export function segmentFor(date: string, startLocal: string, endLocal: string): { left: number; width: number } {
  const start = startLocal.slice(0, 10) < date ? 0 : minutesOf(startLocal);
  const end = endLocal.slice(0, 10) > date ? 1440 : minutesOf(endLocal);
  return { left: (start / 1440) * 100, width: Math.max(((end - start) / 1440) * 100, 0.5) };
}

/** A 24-hour bar for the chosen day: red where the place is taken, green where it is open. */
export function DayTimeline({ timeline, loading }: { timeline?: Timeline; loading?: boolean }) {
  if (!timeline) return <p className="text-sm text-gray-500">{loading ? "Loading..." : "Pick a day to see the times."}</p>;
  return (
    <div className={loading ? "opacity-60" : ""}>
      <h3 className="mb-2 font-semibold">{dayLabel(timeline.date)}</h3>
      <div className="relative h-8 overflow-hidden rounded-md bg-green-100" role="img"
        aria-label={timeline.occupied.length === 0 ? "Open all day" : timeline.occupied.map((o) => o.label).join(" ")}>
        {timeline.occupied.map((o) => {
          const { left, width } = segmentFor(timeline.date, o.start_local, o.end_local);
          return <div key={o.start_local} data-testid="occupied-segment" title={o.label} style={{ left: `${left}%`, width: `${width}%` }}
            className={`absolute inset-y-0 ${o.kind === "booked" ? "bg-red-400" : "bg-gray-400"}`} />;
        })}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-gray-500"><span>12 AM</span><span>6 AM</span><span>12 PM</span><span>6 PM</span><span>12 AM</span></div>
      {timeline.occupied.length === 0 ? (
        <p className="mt-2 text-sm text-green-700">Open all day.</p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm text-gray-700">
          {timeline.occupied.map((o) => <li key={o.start_local}>{o.label}</li>)}
        </ul>
      )}
      <p className="mt-2 text-xs text-gray-500">
        Times are local to the property ({timeline.timezone.replace("_", " ")}).{timeline.rooms > 1 ? " Shown only when every room is taken." : ""}
      </p>
    </div>
  );
}
