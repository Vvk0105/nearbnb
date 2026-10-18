import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mediaUrl } from "../../lib/api";
import { money } from "../../lib/format";
import type { BookingGroup, BookingItem, BookingList } from "../../lib/types";
import { BookingBadge, Countdown } from "../../components/bookings";
import { HostBookingActions } from "../../components/HostBookingActions";
import { ReportProblem, ReviewBox, Stars } from "../../components/Reviews";
import { Alert, Button, Card, TextArea, errorMessage } from "../../components/ui";

const TABS: [BookingGroup, string][] = [["pending", "Needs reply"], ["awaiting_payment", "Awaiting payment"], ["upcoming", "Upcoming"], ["past", "Past"]];

function RequestCard({ b, onDone }: { b: BookingItem; onDone: () => Promise<unknown> }) {
  const [declining, setDeclining] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const g = b.guest;

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    setError("");
    try {
      await api(`/host/bookings/${b.id}/${path}`, { method: "POST", body });
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
      await onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="flex gap-4">
        {b.property.cover_url ? <img src={mediaUrl(b.property.cover_url)} alt="" className="hidden h-24 w-32 shrink-0 rounded-lg object-cover sm:block" /> : null}
        <div className="min-w-0 flex-1 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">{b.property.name} · {b.unit.name}</h3>
            <BookingBadge status={b.status} as="host" />
          </div>
          <p className="text-sm text-gray-800">{b.check_in_label} → {b.check_out_label} <span className="text-gray-500">({b.hours} hours · {b.guests} guest{b.guests > 1 ? "s" : ""}{b.room ? ` · room ${b.room}` : ""})</span></p>
          <p className="text-sm text-gray-800">
            You receive <strong>{money(b.price.host_payable ?? 0, b.currency)}</strong>
            <span className="text-gray-500"> after the {b.price.commission_percent}% platform fee, paid out after the stay</span>
          </p>
          {g && (
            <div className="rounded-lg bg-gray-50 p-3 text-sm">
              <p className="font-medium">{g.full_name}{g.age ? `, ${g.age}` : ""} <span className="ml-1 text-xs font-normal text-green-700">✓ email verified</span></p>
              {g.member_since && <p className="text-xs text-gray-500">Member since {g.member_since.slice(0, 10)}</p>}
              {g.social_links.length > 0 && (
                <p className="mt-1 text-xs">
                  {g.social_links.map((s) => (
                    <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer" className="mr-3 text-brand-700 underline">{s.platform} profile</a>
                  ))}
                </p>
              )}
              {g.reviews && (
                <div className="mt-2 text-xs text-gray-700" aria-label="Guest track record">
                  {g.reviews.count > 0 && g.reviews.average ? <p className="flex items-center gap-2"><Stars value={g.reviews.average} /> from {g.reviews.count} host{g.reviews.count === 1 ? "" : "s"}</p> : <p className="text-gray-500">No reviews from other hosts yet.</p>}
                  <p className="text-gray-500">{g.reviews.completed_stays} stay{g.reviews.completed_stays === 1 ? "" : "s"} completed on NearBnB</p>
                  {g.reviews.items.map((x, n) => <p key={n} className="mt-1 border-l-2 border-gray-200 pl-2">"{x.comment || "No comment"}" <span className="text-gray-400">· {x.rating}/5{x.month ? `, ${x.month}` : ""}</span></p>)}
                </div>
              )}
              {g.email && <p className="mt-1 text-xs text-gray-700">{g.email}{g.phone ? ` · ${g.phone}` : ""}</p>}
              {!g.email && <p className="mt-1 text-xs text-gray-500">Contact details are shared once the guest has paid.</p>}
            </div>
          )}
          {b.status === "requested" && <Countdown target={b.expires_at} seconds={b.seconds_to_respond} label="Reply within" onDone={() => void onDone()} />}
          {b.status === "accepted" && <Countdown target={b.payment_deadline_at} seconds={b.seconds_to_pay} label="Guest must pay within" onDone={() => void onDone()} />}
          {b.closed_reason && b.status === "rejected" && <p className="text-xs text-gray-500">Your reason: {b.closed_reason}</p>}
          <HostBookingActions b={b} />
          {b.status === "completed" && <ReviewBox booking={b} as="host" />}
          <ReportProblem booking={b} as="host" />
          {error && <Alert>{error}</Alert>}
          {b.status === "requested" && !declining && (
            <div className="flex gap-2 pt-1">
              <Button onClick={() => act("accept")} loading={busy}>Accept</Button>
              <Button variant="secondary" onClick={() => setDeclining(true)} disabled={busy}>Decline</Button>
            </div>
          )}
          {b.status === "requested" && declining && (
            <div className="space-y-2 pt-1">
              <TextArea id={`reason-${b.id}`} label="Reason (optional, the guest will see it)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
              <div className="flex gap-2">
                <Button variant="danger" onClick={() => act("reject", { note: note.trim() || null })} loading={busy}>Decline request</Button>
                <Button variant="secondary" onClick={() => setDeclining(false)} disabled={busy}>Back</Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

export function RequestsPage() {
  const queryClient = useQueryClient();
  const [group, setGroup] = useState<BookingGroup>("pending");
  const { data, error, isLoading, refetch } = useQuery({
    queryKey: ["bookings", "host", group],
    queryFn: () => api<BookingList>(`/host/bookings?group=${group}`),
    refetchInterval: 20_000,
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["bookings"] });
    await refetch();
  };
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="text-2xl font-bold">Booking requests</h1>
      <div className="flex flex-wrap gap-2" role="tablist">
        {TABS.map(([key, text]) => (
          <button key={key} role="tab" aria-selected={group === key} onClick={() => setGroup(key)}
            className={`rounded-full border px-3 py-1 text-sm ${group === key ? "border-brand-600 bg-brand-600 text-white" : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"}`}>
            {text}{data?.counts[key] ? ` (${data.counts[key]})` : ""}
          </button>
        ))}
      </div>
      {group === "pending" && <Alert kind="info">Reply quickly: a request expires if you do not answer in time, and the dates are released.</Alert>}
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      {data && data.items.length === 0 && <Card><p className="text-sm text-gray-600">Nothing here right now.</p></Card>}
      {data?.items.map((b) => <RequestCard key={`${b.id}-${b.status}`} b={b} onDone={refresh} />)}
    </div>
  );
}
