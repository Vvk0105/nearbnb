import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../lib/api";
import { money } from "../../lib/format";
import type { InvoiceData } from "../../lib/types";
import { Alert, Button } from "../../components/ui";

/** The receipt and tax invoice. The browser's Print dialog can save it as a PDF. */
export function InvoicePage() {
  const { bookingId } = useParams();
  const { data: inv, error, isLoading } = useQuery({ queryKey: ["invoice", bookingId], queryFn: () => api<InvoiceData>(`/me/bookings/${bookingId}/invoice`), retry: false });

  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !inv) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <div className="mx-auto max-w-xl space-y-3 py-10 text-center">
        <h1 className="text-xl font-bold">{missing ? "No invoice yet" : "Something went wrong"}</h1>
        <p className="text-gray-600">{missing ? "An invoice is issued once your payment is confirmed." : "Please try again."}</p>
        <Link to="/account/trips" className="text-brand-700 hover:underline">Back to my trips</Link>
      </div>
    );
  }
  const cur = inv.currency;
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between print:hidden">
        <Link to={`/account/trips/${bookingId}`} className="text-sm text-brand-700 hover:underline">← Back to my trip</Link>
        <Button variant="secondary" onClick={() => window.print()}>Print or save as PDF</Button>
      </div>
      <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-10 print:border-0 print:shadow-none" aria-label="Tax invoice">
        <p className="mb-6 text-xl font-extrabold tracking-tight">Near<span className="text-brand-600">BnB</span></p>
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-4">
          <div>
            <h1 className="text-2xl font-bold">Tax invoice</h1>
            <p className="text-sm text-gray-600">Receipt for your payment</p>
          </div>
          <dl className="text-right text-sm">
            <div><dt className="inline text-gray-500">Invoice no. </dt><dd className="inline font-mono font-medium">{inv.number}</dd></div>
            <div><dt className="inline text-gray-500">Issued </dt><dd className="inline">{inv.issued_at.slice(0, 10)}</dd></div>
            <div><dt className="inline text-gray-500">Booking </dt><dd className="inline font-mono">{inv.stay.reference}</dd></div>
          </dl>
        </header>

        <section className="grid gap-6 py-4 text-sm sm:grid-cols-2">
          <div>
            <h2 className="mb-1 font-semibold">From</h2>
            <p>{inv.seller.name}</p>
            <p className="text-gray-600">{inv.seller.tax_id}</p>
            <p className="whitespace-pre-wrap text-gray-600">{inv.seller.address}</p>
          </div>
          <div>
            <h2 className="mb-1 font-semibold">To</h2>
            <p>{inv.buyer.name}</p>
            <p className="text-gray-600">{inv.buyer.email}</p>
          </div>
        </section>

        <section className="border-t border-gray-200 py-4 text-sm">
          <h2 className="mb-1 font-semibold">Stay</h2>
          <p>{inv.stay.property} · {inv.stay.unit}</p>
          <p className="text-gray-600">{inv.stay.address}</p>
          <p className="text-gray-600">{inv.stay.check_in} → {inv.stay.check_out} ({inv.stay.hours} hours, {inv.stay.guests} guest{inv.stay.guests > 1 ? "s" : ""}). Times local to the property ({inv.stay.timezone.replace("_", " ")}).</p>
        </section>

        <div className="overflow-x-auto"><table className="w-full border-t border-gray-200 text-sm">
          <thead className="text-left text-xs uppercase text-gray-500">
            <tr><th className="py-2 font-medium">Description</th><th className="py-2 text-right font-medium">Amount</th></tr>
          </thead>
          <tbody>
            {inv.lines.map((l) => (
              <tr key={l.description} className="border-t border-gray-100"><td className="py-2">{l.description}</td><td className="py-2 text-right">{money(l.amount, cur)}</td></tr>
            ))}
            {inv.tax.name && (
              <tr className="border-t border-gray-100">
                <td className="py-2">{inv.tax.name} ({inv.tax.rate_percent}%){inv.tax.inclusive ? ", included in the amounts above" : ""}</td>
                <td className="py-2 text-right">{money(inv.tax.amount, cur)}</td>
              </tr>
            )}
            <tr className="border-t-2 border-gray-300 font-semibold"><td className="py-3">Total paid ({cur})</td><td className="py-3 text-right">{money(inv.total, cur)}</td></tr>
          </tbody>
        </table></div>

        <footer className="mt-4 space-y-1 border-t border-gray-200 pt-4 text-xs text-gray-500">
          <p>Paid {inv.payment.paid_at.slice(0, 10)} by card. Payment reference {inv.payment.reference}.</p>
          <p>{inv.footer}</p>
        </footer>
      </article>
      {inv.seller.tax_id.includes("configured") && <div className="print:hidden"><Alert kind="info">The seller tax details are still placeholders. An admin sets them in Admin &gt; Settings &gt; Invoices.</Alert></div>}
    </div>
  );
}
