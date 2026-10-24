import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, apiUrl } from "../../lib/api";
import { money, todayIso } from "../../lib/format";
import type { AdminRevenue } from "../../lib/types";
import { Alert, Field, errorMessage } from "../../components/ui";
import { AreaChart, BarChart } from "../../components/charts";
import { Icon } from "../../components/icons";
import { EmptyState, Kpi, Panel } from "../../components/dashboardUi";

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (isoDate: string, n: number) => {
  const [y, m, d] = isoDate.split("-").map(Number);
  return iso(new Date(y, m - 1, d + n));
};

export type Preset = { key: string; label: string; range: () => [string, string] };

/** Quick ranges. Every range ends today (or at the end of last month). */
export const PRESETS: Preset[] = [
  { key: "7", label: "Last 7 days", range: () => [addDays(todayIso(), -6), todayIso()] },
  { key: "30", label: "Last 30 days", range: () => [addDays(todayIso(), -29), todayIso()] },
  { key: "90", label: "Last 90 days", range: () => [addDays(todayIso(), -89), todayIso()] },
  { key: "month", label: "This month", range: () => { const t = todayIso(); return [`${t.slice(0, 8)}01`, t]; } },
  {
    key: "lastmonth",
    label: "Last month",
    range: () => {
      const [y, m] = todayIso().split("-").map(Number);
      return [iso(new Date(y, m - 2, 1)), iso(new Date(y, m - 1, 0))];
    },
  },
];

/** What the platform earned, what guests paid, what went back and what went out, for any date range. */
export function AdminRevenuePage() {
  const [preset, setPreset] = useState("30");
  const [from, setFrom] = useState(PRESETS[1].range()[0]);
  const [to, setTo] = useState(PRESETS[1].range()[1]);
  const valid = from !== "" && to !== "" && from <= to;
  const { data, error, isLoading, isFetching } = useQuery({
    queryKey: ["admin-revenue", from, to],
    queryFn: () => api<AdminRevenue>(`/admin/revenue?from=${from}&to=${to}`),
    enabled: valid,
    placeholderData: (prev) => prev,
  });

  const choose = (p: Preset) => {
    const [a, b] = p.range();
    setPreset(p.key);
    setFrom(a);
    setTo(b);
  };
  const cur = data?.currency ?? "AUD";
  const t = data?.totals;
  const series = data?.series ?? [];

  return (
    <div className="space-y-6">
      <Panel
        title="Date range"
        action={valid && <a href={apiUrl(`/admin/revenue.csv?from=${from}&to=${to}`)} download className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-50"><Icon name="download" className="h-4 w-4" /> Download CSV</a>}
      >
        <div className="flex flex-wrap items-end gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              aria-pressed={preset === p.key}
              onClick={() => choose(p)}
              className={`rounded-full border px-3.5 py-1.5 text-sm transition ${preset === p.key ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}
            >
              {p.label}
            </button>
          ))}
          <div className="ml-auto flex flex-wrap items-end gap-2">
            <Field id="rev-from" label="From" type="date" value={from} max={to || undefined} onChange={(e) => { setPreset("custom"); setFrom(e.target.value); }} />
            <Field id="rev-to" label="To" type="date" value={to} min={from || undefined} onChange={(e) => { setPreset("custom"); setTo(e.target.value); }} />
          </div>
        </div>
        {!valid && <div className="mt-3"><Alert>Choose a start date that is on or before the end date.</Alert></div>}
      </Panel>

      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && valid && <p className="text-slate-500">Loading...</p>}

      {t && (
        <div className={`space-y-6 ${isFetching ? "opacity-70" : ""}`}>
          <section aria-label="Revenue summary" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Kpi icon="percent" label="Platform revenue" value={money(t.platform_revenue, cur)} hint={`${money(t.service_fees, cur)} service fees + ${money(t.commission, cur)} commission`} spark={series.map((d) => d.platform_revenue)} />
            <Kpi icon="card" label="Guest payments" value={money(t.guest_payments, cur)} hint="Card payments that succeeded" spark={series.map((d) => d.paid)} />
            <Kpi icon="refund" label="Refunds" value={money(t.refunds, cur)} hint="Refunds made in this period" />
            <Kpi icon="money" label="Net collected" value={money(t.net_collected, cur)} hint="Guest payments minus refunds" />
            <Kpi icon="wallet" label="Paid to hosts" value={money(t.paid_to_hosts, cur)} hint="Sent to hosts, minus any taken back" />
            <Kpi icon="booking" label="Bookings confirmed" value={t.bookings} hint={t.bookings ? `Average ${money(t.average_booking, cur)} each` : "None in this period"} spark={series.map((d) => d.bookings)} />
            <Kpi icon="percent" label="Tax in those bookings" value={money(t.tax, cur)} hint="Tax shown on the bookings (included or added)" />
          </section>

          <div className="grid gap-6 xl:grid-cols-2">
            <Panel title="Platform revenue per day">
              <AreaChart points={series.map((d) => ({ label: d.date, value: d.platform_revenue }))} format={(v) => money(v, cur)} ariaLabel="Platform revenue per day" />
            </Panel>
            <Panel title="Guest payments per day">
              <BarChart points={series.map((d) => ({ label: d.date, value: d.paid }))} format={(v) => money(v, cur)} ariaLabel="Guest payments per day" />
            </Panel>
          </div>

          <Panel title="Top properties">
            {data.top_properties.length === 0 ? (
              <EmptyState icon="home" title="No confirmed bookings in this period" />
            ) : (
              <div className="overflow-x-auto">
                <table className="data-table">
                  <thead><tr><th>Property</th><th>Host</th><th className="text-right">Bookings</th><th className="text-right">Booking value</th><th className="text-right">Platform revenue</th></tr></thead>
                  <tbody>
                    {data.top_properties.map((p) => (
                      <tr key={`${p.property}-${p.host}`}>
                        <td className="font-medium">{p.property}</td>
                        <td>{p.host}</td>
                        <td className="text-right">{p.bookings}</td>
                        <td className="text-right">{money(p.booking_value, cur)}</td>
                        <td className="text-right font-medium">{money(p.platform_revenue, cur)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel title="How these numbers are worked out">
            <ul className="list-disc space-y-1 pl-5 text-sm text-slate-600">
              <li><strong>Guest payments</strong> and <strong>refunds</strong> are counted on the day the payment succeeded or the refund was made.</li>
              <li><strong>Service fees, commission, tax and booking value</strong> come from the price saved on each booking, for bookings <strong>confirmed</strong> in the period that are not cancelled. A later refund does not change them here; it shows in the Refunds line.</li>
              <li><strong>Platform revenue</strong> = service fees + commission. It does not include the host's share or tax.</li>
              <li><strong>Paid to hosts</strong> counts transfers sent to hosts in the period, minus anything taken back.</li>
              <li>All amounts are in {cur}. Days are UTC days.</li>
            </ul>
          </Panel>
        </div>
      )}
    </div>
  );
}
