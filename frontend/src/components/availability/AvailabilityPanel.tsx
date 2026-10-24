import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { dayLabel, money, timeLabel, timeOptions, todayIso } from "../../lib/format";
import type { CalendarMonth, PublicUnit, Quote, Timeline } from "../../lib/types";
import { Alert } from "../ui";
import { DayTimeline } from "./DayTimeline";
import { MonthCalendar } from "./MonthCalendar";
import { RequestToBook } from "./RequestToBook";

/** True for a unit on fixed timing (the host set its check-in and check-out times). Anything else is flexible, as before. */
export function fixedTiming(unit: { stay_mode?: string; fixed_checkin_time?: string | null; fixed_checkout_time?: string | null }): boolean {
  return unit.stay_mode === "fixed" && Boolean(unit.fixed_checkin_time) && Boolean(unit.fixed_checkout_time);
}

export type PanelInitial = { date?: string; time?: string; nights?: number; guests?: number };

export function PriceBreakdownView({ quote }: { quote: Quote }) {
  const p = quote.price!;
  const cur = quote.currency ?? "AUD";
  const per24 = p.nights > 0 ? p.accommodation / p.nights : 0;
  const row = (label: string, value: string, strong = false) => (
    <div className={`flex justify-between ${strong ? "border-t border-gray-200 pt-2 text-base font-semibold" : ""}`}><dt>{label}</dt><dd>{value}</dd></div>
  );
  return (
    <dl className="space-y-1 text-sm text-gray-700" aria-label="Price breakdown">
      {row(`${money(per24, cur)} × ${p.nights} night${p.nights > 1 ? "s" : ""}`, money(p.accommodation, cur))}
      {p.cleaning_fee > 0 && row("Cleaning fee", money(p.cleaning_fee, cur))}
      {p.service_fee > 0 && row("Service fee", money(p.service_fee, cur))}
      {p.tax_name && p.tax > 0 && row(`${p.tax_name} (${p.tax_rate_percent}%)${p.tax_inclusive ? " included" : ""}`, money(p.tax, cur))}
      {row("Total", money(p.total, cur), true)}
    </dl>
  );
}

/** Calendar + day timeline + check-in/duration pickers + an instant answer with price for ONE bookable unit. */
export function AvailabilityPanel({ unit, timezone, initial = {} }: { unit: PublicUnit; timezone: string; initial?: PanelInitial }) {
  const today = todayIso();
  const [date, setDate] = useState(initial.date ?? "");
  const [month, setMonth] = useState((initial.date ?? today).slice(0, 7));
  const [time, setTime] = useState(initial.time ?? "14:00");
  const [nights, setNights] = useState(initial.nights ?? 1);
  const [guests, setGuests] = useState(Math.min(initial.guests ?? 2, unit.capacity));

  const fixed = fixedTiming(unit);
  const times = fixed ? [unit.fixed_checkin_time!] : timeOptions(unit.rules.interval_minutes, unit.checkin_from, unit.checkin_to);
  const maxNights = Math.max(1, Math.floor(unit.rules.max_stay_hours / unit.rules.step_hours));
  const effectiveTime = times.includes(time) ? time : times[0];

  const calendar = useQuery({
    queryKey: ["calendar", unit.id, month],
    queryFn: () => api<CalendarMonth>(`/units/${unit.id}/availability/calendar?month=${month}`),
    placeholderData: keepPreviousData,
  });
  const timeline = useQuery({
    queryKey: ["timeline", unit.id, date],
    queryFn: () => api<Timeline>(`/units/${unit.id}/availability/day?date=${date}`),
    enabled: Boolean(date),
    placeholderData: keepPreviousData,
  });
  const quote = useQuery({
    queryKey: ["quote", unit.id, date, effectiveTime, nights, guests],
    queryFn: () => api<Quote>(`/units/${unit.id}/quote`, { method: "POST", body: { checkin_local: `${date}T${effectiveTime}`, hours: nights * unit.rules.step_hours, guests } }),
    enabled: Boolean(date && effectiveTime),
    placeholderData: keepPreviousData,
  });

  const pick = (isoDate: string) => {
    setDate(isoDate);
    setMonth(isoDate.slice(0, 7));
  };
  const q = quote.data;
  const field = "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none";

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <div className="space-y-6">
        <MonthCalendar month={month} days={calendar.data?.days ?? []} selected={date} onSelect={pick} onMonthChange={setMonth}
          canGoBack={month > today.slice(0, 7)} loading={calendar.isFetching} />
        {calendar.error && <Alert>Could not load the calendar. Please try again.</Alert>}
        <DayTimeline timeline={date ? timeline.data : undefined} loading={timeline.isFetching} />
      </div>

      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <label htmlFor={`d-${unit.id}`} className="mb-1 block text-sm font-medium text-gray-700">Check-in date</label>
            <input id={`d-${unit.id}`} type="date" min={today} value={date} onChange={(e) => e.target.value && pick(e.target.value)} className={field} />
          </div>
          {fixed ? (
            <div className="col-span-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
              Fixed times: <strong>check in {timeLabel(unit.fixed_checkin_time!)}</strong>, <strong>check out {timeLabel(unit.fixed_checkout_time!)}</strong>
            </div>
          ) : (
            <div>
              <label htmlFor={`t-${unit.id}`} className="mb-1 block text-sm font-medium text-gray-700">Check-in time</label>
              <select id={`t-${unit.id}`} value={effectiveTime} onChange={(e) => setTime(e.target.value)} className={field}>
                {times.map((t) => <option key={t} value={t}>{timeLabel(t)}</option>)}
              </select>
            </div>
          )}
          <div>
            <label htmlFor={`n-${unit.id}`} className="mb-1 block text-sm font-medium text-gray-700">{fixed ? "Nights" : "Stay"}</label>
            <select id={`n-${unit.id}`} value={nights} onChange={(e) => setNights(Number(e.target.value))} className={field}>
              {Array.from({ length: maxNights }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{fixed ? `${n} night${n > 1 ? "s" : ""}` : `${n * 24} hours (${n} night${n > 1 ? "s" : ""})`}</option>)}
            </select>
          </div>
          <div className="col-span-2">
            <label htmlFor={`g-${unit.id}`} className="mb-1 block text-sm font-medium text-gray-700">Guests (up to {unit.capacity})</label>
            <input id={`g-${unit.id}`} type="number" min={1} max={unit.capacity} value={guests}
              onChange={(e) => setGuests(Math.max(1, Math.min(unit.capacity, Number(e.target.value) || 1)))} className={field} />
          </div>
        </div>

        <div aria-live="polite" className="rounded-xl border border-gray-200 bg-white p-4">
          {!date && <p className="text-sm text-gray-600">Choose a check-in date to see if it's available and what it costs.</p>}
          {date && quote.isLoading && <p className="text-sm text-gray-500">Checking...</p>}
          {quote.error && <Alert>Could not check availability. Please try again.</Alert>}
          {q && date && q.available && (
            <div className="space-y-3">
              <p className="font-semibold text-green-700">Available{q.rooms_free && q.rooms_free > 1 ? ` · ${q.rooms_free} rooms free` : ""}</p>
              <p className="text-sm text-gray-700">Check in {q.checkin_label} · Check out {q.checkout_label}</p>
              <PriceBreakdownView quote={q} />
            </div>
          )}
          {q && date && !q.available && (
            <div className="space-y-3">
              <p className="font-semibold text-red-700">Not available</p>
              <p className="text-sm text-gray-700">{q.message}</p>
              {q.alternatives.length > 0 && (
                <div>
                  <p className="mb-2 text-sm font-medium">{fixed ? `Other dates that work for ${nights} night${nights > 1 ? "s" : ""}:` : `Other times that work for ${nights * 24} hours:`}</p>
                  <div className="flex flex-wrap gap-2">
                    {q.alternatives.map((a) => (
                      <button key={a.checkin_local} type="button"
                        onClick={() => { pick(a.checkin_local.slice(0, 10)); setTime(a.checkin_local.slice(11, 16)); }}
                        className="rounded-full border border-brand-300 bg-brand-50 px-3 py-1 text-xs font-medium text-brand-800 hover:bg-brand-100">
                        {fixed ? dayLabel(a.checkin_local.slice(0, 10)) : `${dayLabel(a.checkin_local.slice(0, 10))}, ${timeLabel(a.checkin_local.slice(11, 16))}`}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <RequestToBook unit={unit} quote={date && !quote.isPlaceholderData ? q : undefined} timezone={timezone} selection={{ date, time: effectiveTime, nights, guests }} />
      </div>
    </div>
  );
}
