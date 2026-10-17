import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { AdminDashboardData } from "../../lib/types";
import { Alert, Card, errorMessage } from "../../components/ui";

const TONE: Record<string, string> = {
  none: "border-gray-200 bg-white text-gray-500", info: "border-blue-200 bg-blue-50 text-blue-900",
  action: "border-amber-300 bg-amber-50 text-amber-900", problem: "border-red-300 bg-red-50 text-red-900",
};

function Stat({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-lg border border-gray-200 bg-white p-3"><dt className="text-xs text-gray-500">{label}</dt><dd className="text-xl font-semibold">{value}</dd></div>;
}

/** The first thing an admin sees: what needs a decision, what is broken, and how the platform is doing. */
export function AdminDashboardPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-dashboard"], queryFn: () => api<AdminDashboardData>("/admin/dashboard"), refetchInterval: 30_000 });
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  const open = data.alerts.filter((a) => a.count > 0 && a.severity !== "info");
  const cur = "AUD";
  return (
    <div className="space-y-6">
      {open.length === 0 ? <Alert kind="success">Nothing needs your attention right now.</Alert> : (
        <p className="text-sm text-gray-700"><strong>{open.length}</strong> thing{open.length > 1 ? "s" : ""} need{open.length > 1 ? "" : "s"} your attention.</p>
      )}
      <section aria-label="Alerts" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {data.alerts.map((a) => (
          <Link key={a.key} to={a.href} className={`rounded-xl border p-4 transition hover:shadow ${TONE[a.severity]}`}>
            <p className="text-3xl font-bold">{a.count}</p>
            <p className="text-sm">{a.label}</p>
            {a.severity === "problem" && <p className="mt-1 text-xs font-medium">Something is wrong</p>}
          </Link>
        ))}
      </section>

      <Card title="Bookings">
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Requests waiting" value={data.bookings.requests_waiting} />
          <Stat label="Awaiting payment" value={data.bookings.awaiting_payment} />
          <Stat label="Upcoming stays" value={data.bookings.upcoming} />
          <Stat label="In progress" value={data.bookings.in_progress} />
          <Stat label="Completed (30 days)" value={data.bookings.completed_30_days} />
          <Stat label="Cancelled (30 days)" value={data.bookings.cancelled_30_days} />
        </dl>
      </Card>

      <Card title="Money, last 30 days">
        <dl className="grid gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Stat label="Paid by guests" value={money(data.money_30_days.gross_paid, cur)} />
          <Stat label="Refunded" value={money(data.money_30_days.refunded, cur)} />
          <Stat label="Net collected" value={money(data.money_30_days.net_collected, cur)} />
          <Stat label="Service fees earned" value={money(data.money_30_days.service_fees, cur)} />
          <Stat label="Host commission" value={money(data.money_30_days.commission, cur)} />
        </dl>
        <p className="mt-2 text-xs text-gray-500">Fees and commission are for bookings confirmed in the period, before any later refund. Amounts are in the booking currency (AUD).</p>
      </Card>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Totals">
          <dl className="space-y-1 text-sm">
            <div className="flex justify-between"><dt>Users</dt><dd>{data.totals.users}</dd></div>
            <div className="flex justify-between"><dt>Hosts</dt><dd>{data.totals.hosts}</dd></div>
            <div className="flex justify-between"><dt>Live properties</dt><dd>{data.totals.properties}</dd></div>
          </dl>
        </Card>
        <div className="lg:col-span-2">
          <Card title="Recent activity">
            <ul className="space-y-1 text-sm">
              {data.recent.map((r) => (
                <li key={r.id} className="flex flex-wrap justify-between gap-2 border-b border-gray-100 py-1 last:border-0">
                  <span><code className="text-xs">{r.action}</code> {r.actor && <span className="text-gray-500">by {r.actor}</span>}{r.reason && <span className="text-gray-500"> · {r.reason}</span>}</span>
                  <span className="text-xs text-gray-400">{r.at.slice(0, 16).replace("T", " ")}</span>
                </li>
              ))}
              {data.recent.length === 0 && <li className="text-gray-500">Nothing yet.</li>}
            </ul>
            <Link to="/admin/audit" className="mt-3 inline-block text-sm text-brand-700 underline">Open the full audit log</Link>
          </Card>
        </div>
      </div>
    </div>
  );
}
