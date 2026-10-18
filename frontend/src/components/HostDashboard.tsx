import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { money } from "../lib/format";
import type { BookingItem, HostDashboardData } from "../lib/types";
import { Alert, errorMessage } from "./ui";
import { Icon } from "./icons";
import { Kpi, Panel } from "./dashboardUi";

/** One stay: the first check-in day as a little calendar tile, then who, where and what the host earns. */
function Stay({ b, now }: { b: BookingItem; now?: boolean }) {
  // Labels look like "15 Jun 2:00 PM": the day number and month for the little calendar tile.
  const m = /^(\d{1,2}) ([A-Za-z]{3})/.exec(b.check_in_label);
  const day = m?.[1] ?? "";
  const monthName = m?.[2] ?? "";
  return (
    <li className="flex items-center gap-4 py-3">
      <span aria-hidden="true" className={`flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-xl ${now ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700"}`}>
        <span className="text-[10px] font-semibold uppercase leading-none">{now ? "Now" : monthName}</span>
        <span className="text-lg font-semibold leading-tight">{now ? <Icon name="host" className="h-5 w-5" /> : day}</span>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium">{b.property.name} · {b.unit.name}{b.room ? ` · room ${b.room}` : ""}</span>
        <span className="block truncate text-sm text-slate-600">{b.check_in_label} → {b.check_out_label}</span>
        <span className="block text-sm text-slate-500">{b.guest?.full_name}, {b.guests} guest{b.guests > 1 ? "s" : ""}</span>
      </span>
      <span className="text-right">
        <span className="block text-sm font-semibold">{money(b.payout?.amount ?? b.price.host_payable ?? 0, b.currency)}</span>
        <span className="block text-xs text-slate-400">you receive</span>
      </span>
    </li>
  );
}

/** The host's home screen once they are active: what needs doing, who is coming, and the money. */
export function HostDashboard() {
  const { data, error, isLoading } = useQuery({ queryKey: ["host-dashboard"], queryFn: () => api<HostDashboardData>("/host/dashboard"), refetchInterval: 30_000 });
  if (isLoading) return <p className="text-slate-500">Loading your dashboard...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  const listings = Object.entries(data.listings);
  return (
    <div className="space-y-6">
      {data.needs_attention.length > 0 && (
        <div className="space-y-2">
          {data.needs_attention.map((m) => <Alert key={m} kind="info">{m}</Alert>)}
        </div>
      )}

      <section aria-label="Overview" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi to="/host/requests" icon="booking" label="Requests waiting for you" value={data.requests.pending} hint={data.requests.pending > 0 ? "Reply before they expire" : "You are all caught up"} />
        <Kpi to="/host/requests" icon="card" label="Waiting for guest payment" value={data.requests.awaiting_payment} hint="Accepted, not yet paid" />
        <Kpi to="/host/money" icon="money" label="This month's stays" value={money(data.money.this_month)} hint={`${money(data.money.paid_out)} paid out so far`} />
        <Kpi
          to="/host/money"
          icon="wallet"
          label="Next payout"
          value={data.money.next_payout ? money(data.money.next_payout.amount) : "None yet"}
          hint={data.money.next_payout ? `after ${data.money.next_payout.at.slice(0, 10)}` : `${money(data.money.scheduled)} scheduled`}
        />
      </section>

      <div className="grid gap-6 lg:grid-cols-5">
        <div className="space-y-6 lg:col-span-3">
          <Panel title="Guests staying now">
            {data.current_stays.length === 0 ? <p className="text-sm text-slate-500">Nobody is staying right now.</p> : <ul className="divide-y divide-slate-100">{data.current_stays.map((b) => <Stay key={b.id} b={b} now />)}</ul>}
          </Panel>
          <Panel title="Coming up" action={<Link to="/host/requests" className="text-sm text-brand-700 hover:underline">All requests</Link>}>
            {data.upcoming_stays.length === 0 ? <p className="text-sm text-slate-500">No confirmed stays coming up.</p> : <ul className="divide-y divide-slate-100">{data.upcoming_stays.map((b) => <Stay key={b.id} b={b} />)}</ul>}
          </Panel>
        </div>

        <div className="space-y-6 lg:col-span-2">
          <Panel title="Your listings">
            {listings.length === 0 ? <p className="text-sm text-slate-500">No properties yet.</p> : (
              <p className="text-sm text-slate-700">{listings.map(([status, n]) => `${n} ${status.replace(/_/g, " ")}`).join(" · ")}</p>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Link to="/host/properties" className="inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"><Icon name="plus" className="h-4 w-4" /> Manage properties</Link>
              <Link to="/host/money" className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50">Earnings and payouts</Link>
            </div>
          </Panel>
          <Panel title="Tips">
            <ul className="space-y-2 text-sm text-slate-600">
              <li className="flex gap-2"><Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> Reply to requests quickly: unanswered ones expire and free the dates.</li>
              <li className="flex gap-2"><Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> Connect your Airbnb or Booking.com calendar on a unit to avoid double bookings.</li>
              <li className="flex gap-2"><Icon name="check" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" /> Good photos and a clear description get more requests.</li>
            </ul>
          </Panel>
        </div>
      </div>
    </div>
  );
}
