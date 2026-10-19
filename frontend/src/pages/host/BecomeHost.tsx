import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { HostInfo } from "../../lib/types";
import { Alert, Button, errorMessage } from "../../components/ui";
import { Icon, type IconName } from "../../components/icons";

const STEPS = [
  "Fill in a short application and upload a government ID.",
  "Our team reviews it.",
  "Connect a payout account (through Stripe) so we can pay you.",
  "Add your property and rooms. We approve each listing before it goes live.",
];

const BENEFITS: { icon: IconName; title: string; text: string }[] = [
  { icon: "money", title: "You set the price", text: "Choose your price per 24 hours, a cleaning fee and your own cancellation policy." },
  { icon: "check", title: "You decide who stays", text: "Every booking is a request. Accept or decline, and see the guest's rating from other hosts first." },
  { icon: "wallet", title: "Paid securely", text: "Guests pay by card. Your share is sent to your bank through Stripe after the stay begins." },
  { icon: "calendar", title: "Exact-hour availability", text: "Block hours or days, and connect your Airbnb or Booking.com calendar to avoid double bookings." },
];

/** What a person who is not a host yet sees at /host: a short pitch and the way in, on the public site (no host sidebar). */
export function BecomeHost() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      const host = await api<HostInfo>("/host/apply", { method: "POST" });
      queryClient.setQueryData(["host"], host);
      navigate("/host/apply");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-10">
      <section className="rounded-3xl bg-gradient-to-br from-slate-900 via-brand-900 to-brand-700 px-6 py-14 text-center text-white md:px-12">
        <h1 className="mx-auto max-w-2xl text-3xl font-extrabold tracking-tight md:text-5xl">Become a host</h1>
        <p className="mx-auto mt-3 max-w-xl text-lg text-brand-50/90">Share your place for the hours and days you choose, and get paid.</p>
        {error && <div className="mx-auto mt-6 max-w-md text-left"><Alert>{error}</Alert></div>}
        <div className="mt-8">
          <Button onClick={start} loading={busy} className="!bg-brand-500 !px-8 !py-3 !text-base hover:!bg-brand-400">Start application</Button>
        </div>
      </section>

      <section aria-label="Why host" className="grid gap-5 sm:grid-cols-2">
        {BENEFITS.map((b) => (
          <div key={b.title} className="flex gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-700"><Icon name={b.icon} /></span>
            <div>
              <h2 className="font-semibold">{b.title}</h2>
              <p className="mt-0.5 text-sm text-slate-600">{b.text}</p>
            </div>
          </div>
        ))}
      </section>

      <section aria-label="How hosting works" className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold">Hosting on NearBNB takes four steps</h2>
        <ol className="mt-4 space-y-3">
          {STEPS.map((text, i) => (
            <li key={text} className="flex items-start gap-3 text-sm text-slate-700">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-600 text-xs font-semibold text-white">{i + 1}</span>
              <span className="pt-0.5">{text}</span>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
