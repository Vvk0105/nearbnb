import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, apiUrl } from "../../lib/api";
import { money } from "../../lib/format";
import type { HostMoney } from "../../lib/types";
import { Pager } from "../../components/Pager";
import { Alert, Card, Field, errorMessage } from "../../components/ui";

const STATE: Record<string, string> = { scheduled: "Scheduled", held: "On hold", paid: "Paid", failed: "Retrying", reversed: "Taken back", none: "-" };

/** What the host has earned, what is on its way, and what was paid out. The guest's own total and fees are never shown here. */
export function MoneyPage() {
  const [month, setMonth] = useState("");
  const [page, setPage] = useState(1);
  const { data, error, isLoading } = useQuery({
    queryKey: ["host-money", month, page],
    queryFn: () => api<HostMoney>(`/host/money?page=${page}${month ? `&month=${month}` : ""}`),
  });
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  const cur = data.items[0]?.currency ?? "AUD";
  const s = data.summary;
  const csv = apiUrl(`/host/money/export.csv${month ? `?month=${month}` : ""}`);
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <h1 className="text-2xl font-bold">Earnings and payouts</h1>
      <section aria-label="Summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["Paid out so far", money(s.paid_out, cur)],
          ["On its way", money(s.scheduled, cur)],
          ["This month's stays", money(s.this_month, cur)],
          ["Next payout", s.next_payout ? `${money(s.next_payout.amount, cur)} · ${s.next_payout.at.slice(0, 10)}` : "None scheduled"],
        ].map(([label, value]) => <div key={label} className="rounded-xl border border-gray-200 bg-white p-4"><p className="text-xs text-gray-500">{label}</p><p className="text-lg font-semibold">{value}</p></div>)}
      </section>
      {s.deductions > 0 && <Alert kind="info">{money(s.deductions, cur)} has been taken from your earnings for refunds you asked for.</Alert>}

      <Card title="Bookings and earnings">
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field id="month" label="Month of check-in" type="month" value={month} onChange={(e) => { setMonth(e.target.value); setPage(1); }} />
          <a href={csv} download className="mb-0.5 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-800 hover:bg-gray-50">Download CSV</a>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Check-in</th><th className="pr-3">Booking</th><th className="pr-3 text-right">Booking value</th><th className="pr-3 text-right">Commission</th><th className="pr-3 text-right">Your share</th><th className="pr-3 text-right">Refunds</th><th className="pr-3 text-right">You receive</th><th>Payout</th></tr></thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.booking_id} className="border-t border-gray-100 align-top">
                  <td className="py-2 pr-3 whitespace-nowrap">{r.check_in_label.slice(0, 10)}</td>
                  <td className="pr-3">{r.property}<br /><span className="font-mono text-xs text-gray-500">{r.reference}</span>{r.status === "cancelled" && <span className="ml-1 text-xs text-red-700">cancelled</span>}</td>
                  <td className="pr-3 text-right">{money(r.gross, r.currency)}</td>
                  <td className="pr-3 text-right text-gray-600">-{money(r.commission, r.currency)}</td>
                  <td className="pr-3 text-right">{money(r.host_share, r.currency)}</td>
                  <td className="pr-3 text-right text-gray-600">{r.deductions > 0 ? `-${money(r.deductions, r.currency)}` : "-"}</td>
                  <td className="pr-3 text-right font-medium">{money(r.net, r.currency)}</td>
                  <td className="text-xs">{STATE[r.payout_state] ?? r.payout_state}<br /><span className="text-gray-500">{r.payout_state === "paid" ? r.transferred_at?.slice(0, 10) : r.eligible_at?.slice(0, 10)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No earnings{month ? " in this month" : " yet"}.</p>}
        <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />
        <p className="mt-3 text-xs text-gray-500">"Booking value" is the accommodation and cleaning fee. Guest service fees and tax are collected by NearBnB and are not part of your earnings. A payout is sent after check-in plus the payout delay, then Stripe pays it into your bank on its own schedule.</p>
      </Card>

      <Card title="Bank payouts from Stripe">
        {data.bank_payouts.length === 0 ? <p className="text-sm text-gray-500">None yet. Once Stripe pays money into your bank account, it appears here.</p> : (
          <ul className="space-y-1 text-sm">
            {data.bank_payouts.map((p) => <li key={p.id}>{p.arrival_date?.slice(0, 10) ?? "pending"} · {money(p.amount, p.currency)} · {p.status}{p.failure_code ? ` (${p.failure_code})` : ""}</li>)}
          </ul>
        )}
      </Card>
    </div>
  );
}
