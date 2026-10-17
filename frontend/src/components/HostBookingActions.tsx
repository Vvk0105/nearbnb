import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { fromCents, money, toCents } from "../lib/format";
import type { BookingItem } from "../lib/types";
import { Alert, Button, Field, TextArea, errorMessage } from "./ui";

const PAYOUT_TEXT: Record<string, string> = {
  scheduled: "Payout scheduled", held: "Payout on hold", paid: "Paid out to your Stripe account", failed: "Payout failed, we are retrying", reversed: "Payout taken back (refund)", none: "",
};

/** What the host is paid for a confirmed booking, and the two things a host can do about a paid booking. */
export function HostBookingActions({ b }: { b: BookingItem }) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"" | "cancel" | "refund">("");
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const paid = ["confirmed", "checked_in", "completed", "cancelled"].includes(b.status);
  if (!paid) return null;

  const run = async (path: string, body: unknown, message: string) => {
    setBusy(true);
    setError("");
    try {
      await api(`/host/bookings/${b.id}/${path}`, { method: "POST", body });
      setDone(message);
      setMode("");
      setReason("");
      setAmount("");
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const p = b.payout;
  return (
    <div className="space-y-2">
      {p && p.state !== "none" && (
        <p className="text-sm text-gray-800">
          {PAYOUT_TEXT[p.state]}: <strong>{money(p.amount, b.currency)}</strong>
          {p.state === "scheduled" && p.eligible_at && <span className="text-gray-500"> · due after {p.eligible_at.slice(0, 10)}</span>}
          {p.state === "held" && p.hold_note && <span className="text-gray-500"> · {p.hold_note}</span>}
        </p>
      )}
      {b.status === "cancelled" && b.cancelled && (
        <p className="text-xs text-gray-500">Cancelled by {b.cancelled.by === "host" ? "you" : b.cancelled.by === "guest" ? "the guest" : "NearBnB"}{b.cancelled.refund_pct != null && b.cancelled.by === "guest" ? ` (${b.cancelled.refund_pct}% refunded under ${b.cancelled.policy})` : ""}.</p>
      )}
      {done && <Alert kind="success">{done}</Alert>}
      {error && <Alert>{error}</Alert>}
      {mode === "" && (b.status === "confirmed" || b.status === "checked_in" || b.status === "completed") && (
        <div className="flex flex-wrap gap-2 pt-1">
          {b.status === "confirmed" && <Button variant="secondary" onClick={() => setMode("cancel")}>Cancel this booking</Button>}
          <Button variant="secondary" onClick={() => setMode("refund")}>Ask for a guest refund</Button>
        </div>
      )}
      {mode === "cancel" && (
        <div className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-3">
          <p className="text-sm text-red-900">The guest is refunded in full, you are not paid for this booking, and the cancellation is recorded against your account.</p>
          <TextArea id={`cancel-${b.id}`} label="Reason (the guest will see it)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
          <div className="flex gap-2">
            <Button variant="danger" loading={busy} disabled={!reason.trim()} onClick={() => run("cancel", { reason }, "Booking cancelled. The guest has been refunded and told why.")}>Cancel booking and refund guest</Button>
            <Button variant="secondary" disabled={busy} onClick={() => setMode("")}>Keep it</Button>
          </div>
        </div>
      )}
      {mode === "refund" && (
        <div className="space-y-2 rounded-lg border border-gray-200 bg-gray-50 p-3">
          <p className="text-sm text-gray-700">An admin reviews every request. If approved, the amount comes out of your earnings for this booking.</p>
          <Field id={`amount-${b.id}`} label={`Amount (${b.currency})`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} hint={`Up to ${fromCents(b.price.host_payable ?? 0)} of your earnings, or the guest's total.`} />
          <TextArea id={`why-${b.id}`} label="Why should the guest be refunded?" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
          <div className="flex gap-2">
            <Button loading={busy} disabled={!reason.trim() || toCents(amount) <= 0} onClick={() => run("refund-request", { amount: toCents(amount), reason }, "Request sent. An admin will review it and you will get an email.")}>Send request</Button>
            <Button variant="secondary" disabled={busy} onClick={() => setMode("")}>Back</Button>
          </div>
        </div>
      )}
    </div>
  );
}
