import { MONTHS_LONG, dayLabel } from "../../lib/format";
import type { DayStatus } from "../../lib/types";

const STYLE: Record<DayStatus, string> = {
  available: "bg-green-100 text-green-900 hover:bg-green-200",
  partial: "bg-amber-100 text-amber-900 hover:bg-amber-200",
  unavailable: "bg-red-100 text-red-700 line-through hover:bg-red-200",
  past: "text-gray-300 cursor-not-allowed",
  closed: "text-gray-300 cursor-not-allowed",
};
const SPOKEN: Record<DayStatus, string> = {
  available: "available", partial: "partly available", unavailable: "fully booked", past: "in the past", closed: "not open for booking yet",
};

export function monthTitle(month: string): string {
  const [y, m] = month.split("-").map(Number);
  return `${MONTHS_LONG[m - 1]} ${y}`;
}

export function shiftMonth(month: string, delta: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Month grid coloured by availability: green = free all day, amber = partly free, red = fully booked. */
export function MonthCalendar({
  month, days, selected, onSelect, onMonthChange, canGoBack, loading,
}: {
  month: string; days: { date: string; status: DayStatus }[]; selected: string; loading?: boolean;
  onSelect: (date: string) => void; onMonthChange: (month: string) => void; canGoBack: boolean;
}) {
  const [y, m] = month.split("-").map(Number);
  const firstWeekday = (new Date(Date.UTC(y, m - 1, 1)).getUTCDay() + 6) % 7; // Monday first
  const byDate = new Map(days.map((d) => [d.date, d.status]));
  const count = new Date(Date.UTC(y, m, 0)).getUTCDate();

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <button type="button" aria-label="Previous month" disabled={!canGoBack} onClick={() => onMonthChange(shiftMonth(month, -1))}
          className="rounded px-2 py-1 text-lg text-gray-600 hover:bg-gray-100 disabled:opacity-30">‹</button>
        <h3 className="font-semibold" aria-live="polite">{monthTitle(month)}</h3>
        <button type="button" aria-label="Next month" onClick={() => onMonthChange(shiftMonth(month, 1))}
          className="rounded px-2 py-1 text-lg text-gray-600 hover:bg-gray-100">›</button>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center text-xs text-gray-500">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d}>{d}</div>)}
      </div>
      <div className={`mt-1 grid grid-cols-7 gap-1 ${loading ? "opacity-60" : ""}`}>
        {Array.from({ length: firstWeekday }, (_, i) => <div key={`b${i}`} />)}
        {Array.from({ length: count }, (_, i) => {
          const date = `${month}-${String(i + 1).padStart(2, "0")}`;
          const status = byDate.get(date) ?? "closed";
          const disabled = status === "past" || status === "closed";
          return (
            <button key={date} type="button" disabled={disabled} onClick={() => onSelect(date)}
              aria-label={`${dayLabel(date)}, ${SPOKEN[status]}`} aria-pressed={selected === date}
              className={`aspect-square rounded-md text-sm font-medium transition ${STYLE[status]} ${selected === date ? "ring-2 ring-brand-600 ring-offset-1" : ""}`}>
              {i + 1}
            </button>
          );
        })}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
        <li><span className="mr-1 inline-block h-3 w-3 rounded bg-green-200 align-middle" />Free</li>
        <li><span className="mr-1 inline-block h-3 w-3 rounded bg-amber-200 align-middle" />Partly free</li>
        <li><span className="mr-1 inline-block h-3 w-3 rounded bg-red-200 align-middle" />Booked</li>
      </ul>
    </div>
  );
}
