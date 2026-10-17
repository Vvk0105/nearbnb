import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { loadStripe } from "@stripe/stripe-js";
import { Elements, PaymentElement, useElements, useStripe } from "@stripe/react-stripe-js";
import { api, ApiError } from "../../lib/api";
import { money } from "../../lib/format";
import type { BookingItem, PayStart, PriceBreakdown, Quote } from "../../lib/types";
import { PriceBreakdownView } from "../../components/availability/AvailabilityPanel";
import { Countdown } from "../../components/bookings";
import { Alert, Button, Card, errorMessage } from "../../components/ui";

type SyncResult = { outcome: string; booking: BookingItem };

/** After paying: ask the server to read the payment from Stripe, then go to the trip page. */
function useFinish(bookingId: string) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  return async () => {
    try {
      await api<SyncResult>(`/me/bookings/${bookingId}/pay/sync`, { method: "POST" });
    } catch {
      // the webhook will still confirm it; the trip page keeps checking
    }
    await queryClient.invalidateQueries({ queryKey: ["bookings"] });
    navigate(`/account/trips/${bookingId}?paid=1`);
  };
}

function CardForm({ bookingId, amount, currency }: { bookingId: string; amount: number; currency: string }) {
  const stripe = useStripe();
  const elements = useElements();
  const finish = useFinish(bookingId);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!stripe || !elements) return;
    setBusy(true);
    setError("");
    const result = await stripe.confirmPayment({
      elements,
      confirmParams: { return_url: `${window.location.origin}/account/trips/${bookingId}?paid=1` },
      redirect: "if_required", // cards finish here; methods that need a bank page redirect and come back to the return_url
    });
    if (result.error) {
      setError(result.error.message ?? "The payment did not go through. Please try another card.");
      setBusy(false);
      return;
    }
    await finish();
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      <PaymentElement />
      {error && <Alert>{error}</Alert>}
      <Button type="submit" loading={busy} disabled={!stripe || !elements} className="w-full">Pay {money(amount, currency)}</Button>
      <p className="text-center text-xs text-gray-500">Card details go straight to Stripe. NearBnB never sees or stores them.</p>
    </form>
  );
}

function DevPay({ bookingId, amount, currency }: { bookingId: string; amount: number; currency: string }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/me/bookings/${bookingId}/pay/dev-complete`, { method: "POST" });
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
      navigate(`/account/trips/${bookingId}?paid=1`);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3">
      <Alert kind="info">Development mode: no Stripe keys are set, so no card form is shown. This button behaves as if Stripe reported a successful payment.</Alert>
      {error && <Alert>{error}</Alert>}
      <Button onClick={run} loading={busy} className="w-full">Simulate paying {money(amount, currency)}</Button>
    </div>
  );
}

export function PayPage() {
  const { bookingId = "" } = useParams();
  const navigate = useNavigate();
  const [start, setStart] = useState<PayStart | null>(null);
  const [startError, setStartError] = useState<{ message: string; code: string } | null>(null);
  const started = useRef(false);

  const booking = useQuery({ queryKey: ["bookings", "guest", "one", bookingId], queryFn: () => api<BookingItem>(`/me/bookings/${bookingId}`), retry: false });

  useEffect(() => {
    if (started.current || !booking.data) return;
    started.current = true;
    api<PayStart>(`/me/bookings/${bookingId}/pay`, { method: "POST" })
      .then(setStart)
      .catch((e) => setStartError({ message: errorMessage(e), code: e instanceof ApiError ? e.code : "" }));
  }, [booking.data, bookingId]);

  const stripePromise = useMemo(() => (start?.publishable_key ? loadStripe(start.publishable_key) : null), [start?.publishable_key]);

  if (booking.isLoading) return <p className="text-gray-500">Loading...</p>;
  if (booking.error || !booking.data) return <Alert>That booking could not be found. <Link to="/account/trips" className="underline">Back to my trips</Link></Alert>;
  const b = booking.data;

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <Link to={`/account/trips/${b.id}`} className="text-sm text-brand-700 hover:underline">← Back to my trip</Link>
      <h1 className="text-2xl font-bold">Pay for your stay</h1>
      <Card>
        <p className="font-medium">{b.property.name} · {b.unit.name}</p>
        <p className="text-sm text-gray-600">{b.check_in_label} → {b.check_out_label} · {b.guests} guest{b.guests > 1 ? "s" : ""}</p>
        <div className="mt-4"><PriceBreakdownView quote={{ price: b.price as PriceBreakdown, currency: b.currency } as Quote} /></div>
      </Card>
      {start && <Countdown target={b.payment_deadline_at} seconds={start.seconds_to_pay} label="Time left to pay:" onDone={() => navigate(`/account/trips/${b.id}`)} />}
      {startError && (
        <Alert>
          {startError.message} <Link to={`/account/trips/${b.id}`} className="underline">Back to my trip</Link>
        </Alert>
      )}
      {!start && !startError && <p className="text-sm text-gray-500">Preparing payment...</p>}
      {start && start.fake && <DevPay bookingId={b.id} amount={start.amount} currency={start.currency} />}
      {start && !start.fake && !start.publishable_key && (
        <Alert>Card payments are not switched on yet: the Stripe publishable key is missing on the server (STRIPE_PUBLISHABLE_KEY). Please contact support.</Alert>
      )}
      {start && !start.fake && stripePromise && start.client_secret && (
        <Card>
          <Elements stripe={stripePromise} options={{ clientSecret: start.client_secret, appearance: { theme: "stripe", variables: { colorPrimary: "#0d9488" } } }}>
            <CardForm bookingId={b.id} amount={start.amount} currency={start.currency} />
          </Elements>
        </Card>
      )}
    </div>
  );
}
