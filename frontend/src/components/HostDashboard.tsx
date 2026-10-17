import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { money } from "../lib/format";
import type { BookingItem, HostDashboardData } from "../lib/types";
import { Alert, Card, errorMessage } from "./ui";

function Stay({ b }: { b: BookingItem }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-2 border-b border-gray-100 py-2 last:border-0">
      <span><strong>{b.property.name}</strong> · {b.unit.name}{b.room ? ` · room ${b.room}` : ""}<br /><span className="text-sm text-gray-600">{b.check_in_label} → {b.check_out_label} · {b.guest?.full_name}, {b.guests} guest{b.guests > 1 ? "s" : ""}</span></span>
      <span className="text-sm">{money(b.payout?.amount ?? b.price.host_payable ?? 0, b.currency)}</span>
    </li>
  );
}

/** The host's home screen once they are active: what needs doing, who is coming, and the money. */
export function HostDashboard() {
  const { data, error, isLoading } = useQuery({ queryKey: ["host-dashboard"], queryFn: () => api<HostDashboardData>("/host/dashboard"), refetchInterval: 30_000 });
  if (isLoading) return <p className="text-gray-500">Loading your dashboard...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  const listings = Object.entries(data.listings);
  return (
    <div className="space-y-4">
      {data.needs_attention.map((m) => <Alert key={m} kind="info">{m}</Alert>)}
      <section aria-label="Overview" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Link to="/host/requests" className="rounded-xl border border-gray-200 bg-white p-4 hover:shadow"><p className="text-xs text-gray-500">Requests waiting for you</p><p className="text-2xl font-bold">{data.requests.pending}</p></Link>
        <Link to="/host/requests" className="rounded-xl border border-gray-200 bg-white p-4 hover:shadow"><p className="text-xs text-gray-500">Waiting for guest payment</p><p className="text-2xl font-bold">{data.requests.awaiting_payment}</p></Link>
        <Link to="/host/money" className="rounded-xl border border-gray-200 bg-white p-4 hover:shadow"><p className="text-xs text-gray-500">This month's stays</p><p className="text-2xl font-bold">{money(data.money.this_month)}</p></Link>
        <Link to="/host/money" className="rounded-xl border border-gray-200 bg-white p-4 hover:shadow"><p className="text-xs text-gray-500">Next payout</p><p className="text-lg font-bold">{data.money.next_payout ? `${money(data.money.next_payout.amount)}` : "None yet"}</p>{data.money.next_payout && <p className="text-xs text-gray-500">after {data.money.next_payout.at.slice(0, 10)}</p>}</Link>
      </section>
      <Card title="Guests staying now">
        {data.current_stays.length === 0 ? <p className="text-sm text-gray-500">Nobody is staying right now.</p> : <ul>{data.current_stays.map((b) => <Stay key={b.id} b={b} />)}</ul>}
      </Card>
      <Card title="Coming up">
        {data.upcoming_stays.length === 0 ? <p className="text-sm text-gray-500">No confirmed stays coming up.</p> : <ul>{data.upcoming_stays.map((b) => <Stay key={b.id} b={b} />)}</ul>}
      </Card>
      <Card title="Your listings">
        {listings.length === 0 ? <p className="text-sm text-gray-500">No properties yet.</p> : (
          <p className="text-sm text-gray-700">{listings.map(([status, n]) => `${n} ${status.replace(/_/g, " ")}`).join(" · ")}</p>
        )}
        <div className="mt-3 flex gap-3 text-sm"><Link className="text-brand-700 underline" to="/host/properties">Manage properties</Link><Link className="text-brand-700 underline" to="/host/money">Earnings and payouts</Link></div>
      </Card>
    </div>
  );
}
