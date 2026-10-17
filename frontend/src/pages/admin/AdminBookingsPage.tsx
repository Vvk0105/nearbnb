import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { BookingItem, BookingList } from "../../lib/types";
import { BookingBadge } from "../../components/bookings";
import { Alert, Card, Select, errorMessage } from "../../components/ui";

const STATUSES = ["", "requested", "accepted", "confirmed", "rejected", "expired", "withdrawn", "payment_expired"];

type AdminBooking = BookingItem & { host?: { id: string; name: string; email: string } };

export function AdminBookingsPage() {
  const [status, setStatus] = useState("");
  const { data, error, isLoading } = useQuery({
    queryKey: ["bookings", "admin", status],
    queryFn: () => api<BookingList>(`/admin/bookings${status ? `?status=${status}` : ""}`),
    refetchInterval: 30_000,
  });
  return (
    <Card title="Bookings">
      <div className="mb-4 max-w-xs">
        <Select id="bstatus" label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s ? s.replace(/_/g, " ") : "All"}</option>)}
        </Select>
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-gray-500">
            <tr><th className="py-2 pr-3">Ref</th><th className="pr-3">Place</th><th className="pr-3">Guest</th><th className="pr-3">Host</th><th className="pr-3">Stay</th><th className="pr-3">Total</th><th className="pr-3">Payment</th><th>Status</th></tr>
          </thead>
          <tbody>
            {(data?.items as AdminBooking[] | undefined)?.map((b) => (
              <tr key={b.id} className="border-t border-gray-100 align-top">
                <td className="py-2 pr-3 font-mono text-xs">{b.reference}</td>
                <td className="pr-3">{b.property.name}<br /><span className="text-xs text-gray-500">{b.unit.name}{b.room ? ` · room ${b.room}` : ""}</span></td>
                <td className="pr-3">{b.guest?.full_name}<br /><span className="text-xs text-gray-500">{b.guest?.email}</span></td>
                <td className="pr-3">{b.host?.name}<br /><span className="text-xs text-gray-500">{b.host?.email}</span></td>
                <td className="pr-3 whitespace-nowrap">{b.check_in_label}<br />→ {b.check_out_label}</td>
                <td className="pr-3">{money(b.price.total ?? 0, b.currency)}</td>
                <td className="pr-3 text-xs">{b.payment ? <>{b.payment.status}{b.payment.failure_code ? ` (${b.payment.failure_code})` : ""}<br /><span className="font-mono text-gray-500">{b.payment.intent_id?.slice(0, 18)}</span></> : "none"}{b.invoice_number ? <><br />{b.invoice_number}</> : null}</td>
                <td><BookingBadge status={b.status} as="host" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No bookings.</p>}
    </Card>
  );
}
