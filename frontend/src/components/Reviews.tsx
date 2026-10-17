import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { BookingItem, IssueItem, PublicReviews } from "../lib/types";
import { Alert, Button, Card, TextArea, errorMessage } from "./ui";

/** Five stars for a rating (read only). The number is always shown too, so it does not rely on colour. */
export function Stars({ value, label }: { value: number; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1" aria-label={label ?? `${value} out of 5`}>
      <span aria-hidden="true" className="text-amber-500">{"★".repeat(Math.round(value))}<span className="text-gray-300">{"★".repeat(5 - Math.round(value))}</span></span>
      <span className="text-sm text-gray-700">{value.toFixed(1)}</span>
    </span>
  );
}

export function RatingSummary({ average, count }: { average: number | null | undefined; count: number | undefined }) {
  if (!average || !count) return <span className="text-sm text-gray-500">No reviews yet</span>;
  return <span className="inline-flex items-center gap-2"><Stars value={average} /><span className="text-sm text-gray-600">({count} review{count === 1 ? "" : "s"})</span></span>;
}

function StarInput({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div role="radiogroup" aria-label="Rating" className="flex gap-1">
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" role="radio" aria-checked={value === n} aria-label={`${n} star${n > 1 ? "s" : ""}`} onClick={() => onChange(n)}
          className={`rounded px-1 text-3xl leading-none ${n <= value ? "text-amber-500" : "text-gray-300 hover:text-amber-300"}`}>★</button>
      ))}
    </div>
  );
}

/** The review form and the state of a review, for the guest (public review of the stay) or the host (private review of the guest). */
export function ReviewBox({ booking, as }: { booking: BookingItem; as: "guest" | "host" }) {
  const queryClient = useQueryClient();
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const info = booking.review;
  if (!info) return null;

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/${as === "guest" ? "me" : "host"}/bookings/${booking.id}/review`, { method: "POST", body: { rating, comment } });
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const closes = info.closes_at.slice(0, 10);
  const title = as === "guest" ? "Review your stay" : "Review your guest";

  if (info.mine) {
    return (
      <Card title={title}>
        <div className="space-y-2 text-sm">
          <p><Stars value={info.mine.rating} label={`Your rating: ${info.mine.rating} out of 5`} /></p>
          {info.mine.comment && <p className="whitespace-pre-wrap text-gray-700">{info.mine.comment}</p>}
          {info.mine.status === "published" ? (
            <p className="text-green-800">{as === "guest" ? "Your review is now public on the property page." : "Your review is saved. Only other hosts who get a request from this guest, and NearBnB, can see it."}</p>
          ) : info.mine.status === "hidden" ? (
            <p className="text-gray-600">This review was hidden by NearBnB.</p>
          ) : (
            <p className="text-gray-600">Saved. Reviews are shown together: yours appears once {as === "guest" ? "the host has reviewed too" : "the guest has reviewed too"}, or on {closes} when the review period ends. Reviews cannot be changed.</p>
          )}
          {as === "host" && info.theirs && (
            <div className="rounded-lg bg-gray-50 p-3"><p className="mb-1 text-xs text-gray-500">The guest's review of your property (public)</p><Stars value={info.theirs.rating} />{info.theirs.comment && <p className="mt-1 whitespace-pre-wrap">{info.theirs.comment}</p>}</div>
          )}
        </div>
      </Card>
    );
  }
  if (!info.can_review) return <Card title={title}><p className="text-sm text-gray-600">The review period for this stay ended on {closes}.</p></Card>;
  return (
    <Card title={title}>
      <div className="space-y-3">
        <p className="text-sm text-gray-600">
          {as === "guest" ? "Your review will be public. It is shown together with the host's review of you, once both are in, or on " : "Your review of the guest is private: only other hosts who get a request from them, and NearBnB, can see it. Reviews are shown together once both are in, or on "}{closes}.
        </p>
        <StarInput value={rating} onChange={setRating} />
        <TextArea id={`review-${booking.id}`} label="Comment (optional)" value={comment} onChange={(e) => setComment(e.target.value)} maxLength={2000} />
        {error && <Alert>{error}</Alert>}
        <Button onClick={submit} loading={busy} disabled={rating === 0}>Submit review</Button>
        <p className="text-xs text-gray-500">You cannot edit a review after submitting it.</p>
      </div>
    </Card>
  );
}

/** "Report a problem" for a paid booking, plus the reports this person has already made and their outcome. */
export function ReportProblem({ booking, as }: { booking: BookingItem; as: "guest" | "host" }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const categories = useQuery({ queryKey: ["issue-categories"], queryFn: () => api<Record<string, string[]>>("/meta/issue-categories"), staleTime: 300_000, enabled: open });
  const issues: IssueItem[] = booking.issues ?? [];
  if (!booking.can_report_issue && issues.length === 0) return null;

  const send = async () => {
    setBusy(true);
    setError("");
    try {
      await api(`/${as === "guest" ? "me" : "host"}/bookings/${booking.id}/issues`, { method: "POST", body: { category, body } });
      setOpen(false);
      setCategory("");
      setBody("");
      await queryClient.invalidateQueries({ queryKey: ["bookings"] });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const options = categories.data?.[as] ?? [];
  return (
    <Card title="Problems">
      {issues.length > 0 && (
        <ul className="mb-3 space-y-3 text-sm">
          {issues.map((i) => (
            <li key={i.id} className="rounded-lg border border-gray-200 p-3">
              <p className="font-medium">{i.category.replace(/_/g, " ")} <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-normal">{i.status.replace(/_/g, " ")}</span></p>
              <p className="mt-1 whitespace-pre-wrap text-gray-700">{i.body}</p>
              {i.resolution && <p className="mt-2 rounded bg-green-50 p-2 text-green-900"><strong>From NearBnB:</strong> {i.resolution}</p>}
            </li>
          ))}
        </ul>
      )}
      {booking.can_report_issue && !open && <Button variant="secondary" onClick={() => setOpen(true)}>Report a problem</Button>}
      {open && (
        <div className="space-y-3">
          <div>
            <label htmlFor={`cat-${booking.id}`} className="mb-1 block text-sm font-medium text-gray-700">What kind of problem?</label>
            <select id={`cat-${booking.id}`} value={category} onChange={(e) => setCategory(e.target.value)} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
              <option value="">Choose...</option>
              {options.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
            </select>
          </div>
          <TextArea id={`issue-${booking.id}`} label="What happened?" value={body} onChange={(e) => setBody(e.target.value)} maxLength={3000} hint="A sentence or two is enough. Our team will email you the outcome." />
          {error && <Alert>{error}</Alert>}
          <div className="flex gap-2">
            <Button onClick={send} loading={busy} disabled={!category || body.trim().length < 10}>Send report</Button>
            <Button variant="secondary" disabled={busy} onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>
      )}
    </Card>
  );
}

/** Public reviews on a property page, newest first, with "show more". */
export function PropertyReviews({ propertyId }: { propertyId: string }) {
  const [pages, setPages] = useState(1);
  const { data } = useQuery({
    queryKey: ["property-reviews", propertyId, pages],
    queryFn: async () => {
      const all: PublicReviews[] = [];
      for (let p = 1; p <= pages; p++) all.push(await api<PublicReviews>(`/properties/${propertyId}/reviews?page=${p}`));
      return all;
    },
  });
  const first = data?.[0];
  if (!first || first.count === 0) return null;
  const items = data!.flatMap((d) => d.items);
  return (
    <section aria-label="Reviews">
      <h2 className="mb-2 text-lg font-semibold">Guest reviews <span className="ml-2 text-base font-normal"><RatingSummary average={first.average} count={first.count} /></span></h2>
      <ul className="space-y-4">
        {items.map((r) => (
          <li key={r.id} className="border-b border-gray-100 pb-3 last:border-0">
            <p className="text-sm"><strong>{r.author}</strong> <span className="text-gray-500">· {r.month}</span></p>
            <Stars value={r.rating} />
            {r.comment && <p className="mt-1 whitespace-pre-wrap text-sm text-gray-700">{r.comment}</p>}
          </li>
        ))}
      </ul>
      {items.length < first.total && <button type="button" className="mt-3 text-sm text-brand-700 underline" onClick={() => setPages(pages + 1)}>Show more reviews</button>}
    </section>
  );
}
