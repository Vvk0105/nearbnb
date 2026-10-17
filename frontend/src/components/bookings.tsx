import { useEffect, useState } from "react";
import type { BookingStatus } from "../lib/types";

/** Counts down to `target` (an ISO time) and calls `onDone` once when it reaches zero. Uses the server's clock offset. */
export function useSecondsLeft(target: string | null | undefined, initialSeconds: number | null | undefined, onDone?: () => void): number | null {
  const [left, setLeft] = useState<number | null>(initialSeconds ?? null);
  useEffect(() => {
    if (!target) return setLeft(null);
    // Anchor on the server-supplied seconds so a wrong clock on the visitor's computer does not matter.
    const startedAt = Date.now();
    const base = initialSeconds ?? Math.max(0, Math.round((new Date(target).getTime() - Date.now()) / 1000));
    let finished = false;
    const tick = () => {
      const remaining = Math.max(0, base - Math.floor((Date.now() - startedAt) / 1000));
      setLeft(remaining);
      if (remaining === 0 && !finished) {
        finished = true;
        onDone?.();
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, initialSeconds]);
  return left;
}

export function formatCountdown(seconds: number): string {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  return h > 0 ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}:${String(s).padStart(2, "0")}`;
}

export function Countdown({ target, seconds, label, onDone }: { target: string | null; seconds: number | null; label: string; onDone?: () => void }) {
  const left = useSecondsLeft(target, seconds, onDone);
  if (left === null) return null;
  const urgent = left < 300;
  return (
    <p className={`text-sm ${urgent ? "font-semibold text-red-700" : "text-gray-700"}`} aria-live="off">
      {label} <span className="font-mono">{formatCountdown(left)}</span>
    </p>
  );
}

const LABEL: Record<BookingStatus, { guest: string; host: string; tone: string }> = {
  requested: { guest: "Waiting for host", host: "Needs your reply", tone: "bg-amber-100 text-amber-800" },
  accepted: { guest: "Accepted, payment due", host: "Accepted, waiting for payment", tone: "bg-blue-100 text-blue-800" },
  payment_pending: { guest: "Payment processing", host: "Payment processing", tone: "bg-blue-100 text-blue-800" },
  confirmed: { guest: "Confirmed", host: "Confirmed", tone: "bg-green-100 text-green-800" },
  checked_in: { guest: "Checked in", host: "Checked in", tone: "bg-green-100 text-green-800" },
  completed: { guest: "Completed", host: "Completed", tone: "bg-gray-100 text-gray-700" },
  rejected: { guest: "Declined", host: "Declined", tone: "bg-red-100 text-red-800" },
  expired: { guest: "Expired", host: "Expired", tone: "bg-gray-200 text-gray-700" },
  withdrawn: { guest: "Withdrawn", host: "Withdrawn by guest", tone: "bg-gray-200 text-gray-700" },
  payment_expired: { guest: "Payment time ran out", host: "Guest did not pay", tone: "bg-gray-200 text-gray-700" },
  cancelled: { guest: "Cancelled", host: "Cancelled", tone: "bg-gray-200 text-gray-700" },
};

export function BookingBadge({ status, as = "guest" }: { status: BookingStatus; as?: "guest" | "host" }) {
  const l = LABEL[status] ?? { guest: status, host: status, tone: "bg-gray-100 text-gray-700" };
  return <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${l.tone}`}>{l[as]}</span>;
}
