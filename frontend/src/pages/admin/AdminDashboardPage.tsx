import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { AdminAlert, AdminDashboardData } from "../../lib/types";
import { Alert, errorMessage } from "../../components/ui";
import { AreaChart, BarChart, ProgressBar } from "../../components/charts";
import { Icon } from "../../components/icons";
import { Kpi, Panel } from "../../components/dashboardUi";

const DOT: Record<string, string> = { problem: "bg-red-500", action: "bg-amber-500", info: "bg-sky-400", none: "bg-slate-300" };
const ROW: Record<string, string> = {
  problem: "border-red-200 bg-red-50 text-red-900 hover:bg-red-100",
  action: "border-amber-200 bg-amber-50 text-amber-900 hover:bg-amber-100",
  info: "border-slate-200 bg-white text-slate-700 hover:bg-slate-50",
  none: "border-slate-200 bg-white text-slate-500 hover:bg-slate-50",
};
const ORDER: Record<string, number> = { problem: 0, action: 1, info: 2, none: 3 };

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl bg-slate-50 p-3">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="text-lg font-semibold">{value}</dd>
    </div>
  );
}

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function AlertRow({ a }: { a: AdminAlert }) {
  return (
    <Link to={a.href} className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-sm transition ${ROW[a.severity]}`}>
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${DOT[a.severity]}`} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block truncate">{a.label}</span>
        {a.severity === "problem" && <span className="block text-xs font-medium">Something is wrong</span>}
      </span>
      <span className="text-lg font-semibold tabular-nums">{a.count}</span>
      <Icon name="arrow" className="h-4 w-4 shrink-0 opacity-50" />
    </Link>
  );
}

/** The first thing an admin sees: what needs a decision, what is broken, and how the platform is doing. */
export function AdminDashboardPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-dashboard"], queryFn: () => api<AdminDashboardData>("/admin/dashboard"), refetchInterval: 30_000 });
  if (isLoading) return <p className="text-slate-500">Loading...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;

  const cur = "AUD";
  const trend = data.trend ?? [];
  const open = data.alerts.filter((a) => a.count > 0 && a.severity !== "info");
  const sorted = [...data.alerts].filter((a) => a.count > 0).sort((a, b) => ORDER[a.severity] - ORDER[b.severity]);
  const bookingsLast30 = trend.reduce((s, t) => s + t.bookings, 0);
  const stages = [
    { label: "Requests waiting", value: data.bookings.requests_waiting, tone: "amber" as const },
    { label: "Awaiting payment", value: data.bookings.awaiting_payment, tone: "amber" as const },
    { label: "Upcoming stays", value: data.bookings.upcoming, tone: "brand" as const },
    { label: "In progress", value: data.bookings.in_progress, tone: "brand" as const },
    { label: "Completed (30 days)", value: data.bookings.completed_30_days, tone: "slate" as const },
    { label: "Cancelled (30 days)", value: data.bookings.cancelled_30_days, tone: "red" as const },
  ];
  const stageMax = Math.max(1, ...stages.map((s) => s.value));
  const m = data.money_30_days;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-2xl font-semibold tracking-tight">{greeting()}</p>
          {open.length === 0 ? <Alert kind="success">Nothing needs your attention right now.</Alert> : (
            <p className="mt-1 text-sm text-slate-600"><strong>{open.length}</strong> thing{open.length > 1 ? "s" : ""} need{open.length > 1 ? "" : "s"} your attention.</p>
          )}
        </div>
        <p className="text-xs text-slate-400">Updates every 30 seconds</p>
      </div>

      <section aria-label="Key numbers" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Net collected (30 days)" value={money(m.net_collected, cur)} icon="money" spark={trend.map((t) => t.paid)} hint={`${money(m.gross_paid, cur)} paid, ${money(m.refunded, cur)} refunded`} />
        <Kpi label="Booking requests (30 days)" value={bookingsLast30} icon="booking" spark={trend.map((t) => t.bookings)} hint={`${data.bookings.upcoming} upcoming stays`} />
        <Kpi label="Platform revenue (30 days)" value={money(m.service_fees + m.commission, cur)} icon="percent" hint={`${money(m.service_fees, cur)} service fees + ${money(m.commission, cur)} commission`} />
        <Kpi label="Live properties" value={data.totals.properties} icon="home" hint={`${data.totals.hosts} hosts, ${data.totals.users} users`} />
      </section>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel title="Needs attention" className="xl:col-span-1 xl:row-span-2" action={<span className="text-xs text-slate-400">{sorted.length} open</span>}>
          <section aria-label="Alerts" className="space-y-2">
            {sorted.length === 0 && <p className="text-sm text-slate-500">All clear. Nothing is waiting for you.</p>}
            {sorted.map((a) => <AlertRow key={a.key} a={a} />)}
            {data.alerts.some((a) => a.count === 0) && (
              <details className="pt-1 text-sm text-slate-500">
                <summary className="cursor-pointer select-none">{data.alerts.filter((a) => a.count === 0).length} areas are clear</summary>
                <ul className="mt-2 space-y-1">
                  {data.alerts.filter((a) => a.count === 0).map((a) => <li key={a.key}><Link to={a.href} className="hover:underline">{a.label}</Link></li>)}
                </ul>
              </details>
            )}
          </section>
        </Panel>

        <Panel title="Paid by guests, per day" className="xl:col-span-2" action={<span className="text-xs text-slate-400">Last 30 days</span>}>
          {trend.length > 0 ? <AreaChart points={trend.map((t) => ({ label: t.date, value: t.paid }))} format={(v) => money(v, cur)} ariaLabel="Money paid by guests per day" /> : <p className="text-sm text-slate-500">No data yet.</p>}
        </Panel>

        <Panel title="Booking requests, per day" className="xl:col-span-2" action={<span className="text-xs text-slate-400">Last 30 days</span>}>
          {trend.length > 0 ? <BarChart points={trend.map((t) => ({ label: t.date, value: t.bookings }))} ariaLabel="Booking requests per day" /> : <p className="text-sm text-slate-500">No data yet.</p>}
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Bookings">
          <ul className="space-y-3">
            {stages.map((s) => (
              <li key={s.label}>
                <div className="mb-1 flex justify-between text-sm"><span className="text-slate-600">{s.label}</span><span className="font-semibold tabular-nums">{s.value}</span></div>
                <ProgressBar value={s.value} max={stageMax} tone={s.tone} />
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Money, last 30 days" action={<Link to="/admin/revenue" className="text-sm text-brand-700 hover:underline">Open revenue</Link>}>
          <dl className="grid grid-cols-2 gap-3">
            <Stat label="Paid by guests" value={money(m.gross_paid, cur)} />
            <Stat label="Refunded" value={money(m.refunded, cur)} />
            <Stat label="Net collected" value={money(m.net_collected, cur)} />
            <Stat label="Service fees earned" value={money(m.service_fees, cur)} />
            <Stat label="Host commission" value={money(m.commission, cur)} />
          </dl>
          <p className="mt-3 text-xs text-slate-500">Fees and commission are for bookings confirmed in the period, before any later refund. Amounts are in the booking currency (AUD).</p>
        </Panel>
      </div>

      <Panel title="Recent activity" action={<Link to="/admin/audit" className="text-sm text-brand-700 hover:underline">Open the full audit log</Link>}>
        <ul className="divide-y divide-slate-100 text-sm">
          {data.recent.map((r) => (
            <li key={r.id} className="flex items-center gap-3 py-2.5">
              <span aria-hidden="true" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">{(r.actor ?? "S").slice(0, 1).toUpperCase()}</span>
              <span className="min-w-0 flex-1">
                <code className="text-xs">{r.action}</code>
                <span className="block truncate text-xs text-slate-500">{r.actor ? `by ${r.actor}` : "by the system"}{r.reason ? ` · ${r.reason}` : ""}</span>
              </span>
              <span className="shrink-0 text-xs text-slate-400">{r.at.slice(0, 16).replace("T", " ")}</span>
            </li>
          ))}
          {data.recent.length === 0 && <li className="py-2 text-slate-500">Nothing yet.</li>}
        </ul>
      </Panel>
    </div>
  );
}
