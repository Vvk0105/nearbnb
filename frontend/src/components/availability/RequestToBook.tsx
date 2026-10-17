import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { money } from "../../lib/format";
import type { BookingItem, PublicUnit, Quote, Readiness } from "../../lib/types";
import { Alert, Button, errorMessage } from "../ui";

export type Selection = { date: string; time: string; nights: number; guests: number };

/** The "Request to book" button and its confirmation step. Nothing is charged: the host replies first, payment comes after. */
export function RequestToBook({ unit, quote, selection, timezone }: { unit: PublicUnit; quote: Quote | undefined; selection: Selection; timezone: string }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const available = Boolean(selection.date && quote?.available);

  const readiness = useQuery({
    queryKey: ["booking-readiness", user?.id],
    queryFn: () => api<Readiness>("/me/booking-readiness"),
    enabled: Boolean(user) && confirming,
    staleTime: 0,
  });

  // Where to come back to after logging in, so the guest's choices are still on screen.
  const returnTo = () => {
    const p = new URLSearchParams({ date: selection.date, time: selection.time, nights: String(selection.nights), guests: String(selection.guests) });
    return `${location.pathname}?${p.toString()}`;
  };

  const start = () => {
    setError("");
    if (!user) return navigate("/login", { state: { from: returnTo() } });
    setConfirming(true);
  };

  const send = async () => {
    setBusy(true);
    setError("");
    try {
      const booking = await api<BookingItem>(`/units/${unit.id}/book`, {
        method: "POST",
        body: { checkin_local: `${selection.date}T${selection.time}`, hours: selection.nights * unit.rules.step_hours, guests: selection.guests },
      });
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
      await queryClient.invalidateQueries({ queryKey: ["calendar", unit.id] });
      navigate(`/account/trips/${booking.id}`);
    } catch (e) {
      setError(errorMessage(e));
      setConfirming(false);
      // the dates may have just been taken: show the guest the fresh answer
      await queryClient.invalidateQueries({ queryKey: ["quote", unit.id] });
      await queryClient.invalidateQueries({ queryKey: ["calendar", unit.id] });
    } finally {
      setBusy(false);
    }
  };

  if (confirming && quote?.available && quote.price) {
    const r = readiness.data;
    const minutes = quote.response_minutes;
    return (
      <div className="space-y-3 rounded-xl border border-brand-200 bg-brand-50 p-4" aria-label="Confirm your request">
        <h3 className="font-semibold">Send your request</h3>
        <p className="text-sm text-gray-800">{unit.name}</p>
        <p className="text-sm text-gray-800">{quote.checkin_label} → {quote.checkout_label}</p>
        <p className="text-sm text-gray-800">{selection.guests} guest{selection.guests > 1 ? "s" : ""} · Total {money(quote.price.total, quote.currency)}</p>
        <p className="text-xs text-gray-600">
          You are not charged now. The host has {minutes ? `${minutes} minutes` : "a short time"} to accept. If they accept, you will get an email and a short time to pay.
        </p>
        {readiness.isLoading && <p className="text-sm text-gray-500">Checking your profile...</p>}
        {r && !r.ready && (
          <Alert>
            Before you can request a booking, please add: {r.missing.map((m) => m.label).join(", ")}
            {!r.email_verified && " (and verify your email)"}.{" "}
            <Link to="/account" className="font-medium underline">Update my profile</Link>
          </Alert>
        )}
        <div className="flex gap-2">
          <Button onClick={send} loading={busy} disabled={!r?.ready}>Send request</Button>
          <Button variant="secondary" onClick={() => setConfirming(false)} disabled={busy}>Cancel</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {error && <Alert>{error}</Alert>}
      <Button className="w-full" disabled={!available} onClick={start}>Request to book</Button>
      <p className="text-center text-xs text-gray-500">
        {user ? "No payment yet: the host replies first." : "You'll log in first. Your choices are kept."} Times are local to the property ({timezone.replace("_", " ")}).
      </p>
    </div>
  );
}
