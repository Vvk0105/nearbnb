import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../lib/api";
import { money } from "../lib/format";
import type { BookingItem, CancelPreview, PolicyTier } from "../lib/types";
import { Alert, Button, Card, errorMessage } from "./ui";

/** "Full refund until 5 days before check-in, 50% until 1 day before, then nothing." in words. */
export function describeTiers(tiers: PolicyTier[]): string[] {
  const when = (h: number) => (h % 24 === 0 && h >= 24 ? `${h / 24} day${h / 24 === 1 ? "" : "s"}` : `${h} hours`);
  if (tiers.length === 0) return ["No refund once the booking is confirmed."];
  const sorted = [...tiers].sort((a, b) => b.hours_before - a.hours_before);
  const lines = sorted.map((t) => `${t.refund_pct}% refund of the accommodation if you cancel at least ${when(t.hours_before)} before check-in`);
  lines.push(`No refund after that`);
  return lines;
}

/** The guest's cancel button: shows exactly what they would get back, then asks them to confirm that amount. */
export function CancelBooking({ booking }: { booking: BookingItem }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preview = useQuery({
    queryKey: ["cancel-preview", booking.id],
    queryFn: () => api<CancelPreview>(`/me/bookings/${booking.id}/cancel-preview`),
    enabled: open,
    staleTime: 0,
    gcTime: 0,
  });

  const confirm = async (expected: number) => {
    setBusy(true);
    setError("");
    try {
      await api(`/me/bookings/${booking.id}/cancel`, { method: "POST", body: { expected_refund: expected } });
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
      setOpen(false);
    } catch (e) {
      setError(errorMessage(e));
      if (e instanceof ApiError && e.code === "REFUND_CHANGED") await preview.refetch(); // show the new amount, ask again
    } finally {
      setBusy(false);
    }
  };

  if (!open) return <Button variant="secondary" onClick={() => setOpen(true)}>Cancel this booking</Button>;
  const p = preview.data;
  const cur = booking.currency;
  return (
    <Card title="Cancel this booking">
      {preview.isLoading && <p className="text-sm text-gray-500">Working out your refund...</p>}
      {preview.error && <Alert>{errorMessage(preview.error)}</Alert>}
      {p && (
        <div className="space-y-4">
          <div>
            <p className="text-sm font-medium">{p.policy.name} cancellation policy</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-gray-600">{describeTiers(p.policy.tiers).map((l) => <li key={l}>{l}</li>)}</ul>
          </div>
          <p className="text-sm text-gray-700">
            Check-in is in about {p.refund.hours_before >= 48 ? `${Math.round(p.refund.hours_before / 24)} days` : `${Math.max(0, Math.round(p.refund.hours_before))} hours`}, so you would get{" "}
            <strong>{p.refund.refund_pct}%</strong> of the accommodation back.
          </p>
          <dl className="space-y-1 text-sm" aria-label="Refund breakdown">
            <div className="flex justify-between"><dt>You paid</dt><dd>{money(p.paid, cur)}</dd></div>
            <div className="flex justify-between"><dt>Accommodation refunded</dt><dd>{money(p.refund.accommodation, cur)}</dd></div>
            {p.refund.cleaning_fee > 0 && <div className="flex justify-between"><dt>Cleaning fee refunded</dt><dd>{money(p.refund.cleaning_fee, cur)}</dd></div>}
            <div className="flex justify-between"><dt>Service fee{p.service_fee_refundable ? "" : " (not refundable)"}</dt><dd>{money(p.refund.service_fee, cur)}</dd></div>
            {p.refund.tax > 0 && <div className="flex justify-between"><dt>Tax on the refunded amount</dt><dd>{money(p.refund.tax, cur)}</dd></div>}
            <div className="flex justify-between border-t border-gray-200 pt-2 text-base font-semibold"><dt>Refund to your card</dt><dd>{money(p.refund.total, cur)}</dd></div>
          </dl>
          <p className="text-xs text-gray-500">Refunds go back to the card you paid with and can take 5 to 10 days to appear. This cannot be undone.</p>
          {error && <Alert>{error}</Alert>}
          <div className="flex gap-2">
            <Button variant="danger" loading={busy} onClick={() => confirm(p.refund.total)}>
              {p.refund.total > 0 ? `Cancel and refund ${money(p.refund.total, cur)}` : "Cancel with no refund"}
            </Button>
            <Button variant="secondary" disabled={busy} onClick={() => setOpen(false)}>Keep my booking</Button>
          </div>
        </div>
      )}
      {!p && !preview.isLoading && !preview.error && error && <Alert>{error}</Alert>}
    </Card>
  );
}
