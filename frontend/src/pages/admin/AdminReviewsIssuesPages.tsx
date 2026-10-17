import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { AdminIssueRow, AdminReviewRow, Paged } from "../../lib/types";
import { Pager } from "../../components/Pager";
import { Stars } from "../../components/Reviews";
import { Alert, Button, Card, Field, Select, TextArea, errorMessage } from "../../components/ui";

function ReviewCard({ r, onDone }: { r: AdminReviewRow; onDone: () => Promise<unknown> }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (path: "hide" | "restore") => {
    setBusy(true);
    setError("");
    try {
      await api(`/admin/reviews/${r.id}/${path}`, { method: "POST", body: { reason } });
      setReason("");
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="space-y-2 rounded-lg border border-gray-200 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span><Stars value={r.rating} /> <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs">{r.direction === "guest_to_host" ? "guest about the stay (public)" : "host about the guest (private)"}</span></span>
        <span className="text-xs text-gray-500">{r.status}{r.hidden_reason ? `: ${r.hidden_reason}` : ""}</span>
      </div>
      <p className="whitespace-pre-wrap text-gray-800">{r.comment || <em className="text-gray-500">No comment</em>}</p>
      <p className="text-xs text-gray-500">{r.author} ({r.author_email}) about {r.subject} · {r.property} · <Link className="text-brand-700 underline" to={`/admin/bookings/${r.booking_id}`}>{r.reference}</Link></p>
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-64 flex-1"><Field id={`why-${r.id}`} label={r.status === "hidden" ? "Note (optional)" : "Reason to hide (required, recorded)"} value={reason} onChange={(e) => setReason(e.target.value)} /></div>
        {r.status === "hidden"
          ? <Button loading={busy} onClick={() => act("restore")}>Restore</Button>
          : <Button variant="danger" loading={busy} disabled={!reason.trim()} onClick={() => act("hide")}>Hide review</Button>}
      </div>
    </li>
  );
}

export function AdminReviewsPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("");
  const [direction, setDirection] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), ...(status ? { status } : {}), ...(direction ? { direction } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-reviews", status, direction, q, page], queryFn: () => api<Paged<AdminReviewRow>>(`/admin/reviews?${params}`) });
  return (
    <Card title="Reviews">
      <p className="mb-3 text-sm text-gray-600">Guest reviews of a stay are public once published. Host reviews of guests are private. Hiding a review removes it everywhere, including from the property's rating; restoring puts it back.</p>
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Field id="rq" label="Search" placeholder="Comment, reference or author" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <Select id="rstatus" label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">Any</option><option value="published">Published</option><option value="submitted">Waiting to publish</option><option value="hidden">Hidden</option>
        </Select>
        <Select id="rdir" label="Type" value={direction} onChange={(e) => { setDirection(e.target.value); setPage(1); }}>
          <option value="">Both</option><option value="guest_to_host">Guest about the stay</option><option value="host_to_guest">Host about the guest</option>
        </Select>
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <ul className="space-y-3">{data?.items.map((r) => <ReviewCard key={`${r.id}-${r.status}`} r={r} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-reviews"] })} />)}</ul>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No reviews match.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}

function IssueCard({ i, onDone }: { i: AdminIssueRow; onDone: () => Promise<unknown> }) {
  const [note, setNote] = useState(i.admin_note ?? "");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const closed = i.status === "resolved" || i.status === "dismissed";
  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    try {
      await api(`/admin/issues/${i.id}`, { method: "PATCH", body });
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="space-y-3 rounded-lg border border-gray-200 p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">{i.category.replace(/_/g, " ")} · reported by the {i.role} <span className="ml-2 rounded-full bg-gray-100 px-2 py-0.5 text-xs font-normal">{i.status.replace(/_/g, " ")}</span>{i.blocks_payout && <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-normal text-amber-900">payout on hold</span>}</p>
        <span className="text-xs text-gray-500">{i.created_at.slice(0, 16).replace("T", " ")}</span>
      </div>
      <p className="whitespace-pre-wrap rounded bg-gray-50 p-3">{i.body}</p>
      <p className="text-xs text-gray-500">
        <Link className="font-mono text-brand-700 underline" to={`/admin/bookings/${i.booking_id}`}>{i.reference}</Link> · {i.property} · reporter {i.reporter.name} ({i.reporter.email}) · guest {i.guest.name} · host {i.host.name} ({i.host.email})
      </p>
      {closed ? (
        <div className="space-y-1"><p><strong>Outcome sent to the reporter:</strong> {i.resolution}</p>{i.admin_note && <p className="text-gray-600"><strong>Internal note:</strong> {i.admin_note}</p>}</div>
      ) : (
        <div className="space-y-3">
          <TextArea id={`note-${i.id}`} label="Internal note (never shown to the reporter)" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
          <label className="flex items-center gap-2"><input type="checkbox" checked={i.blocks_payout} disabled={busy} onChange={(e) => save({ blocks_payout: e.target.checked })} /> Hold the host payout for this booking while this is open</label>
          <TextArea id={`res-${i.id}`} label="Message to the reporter (required to resolve or dismiss)" rows={2} value={resolution} onChange={(e) => setResolution(e.target.value)} />
          {error && <Alert>{error}</Alert>}
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" loading={busy} onClick={() => save({ admin_note: note })}>Save note</Button>
            {i.status === "open" && <Button variant="secondary" loading={busy} onClick={() => save({ status: "in_progress", admin_note: note })}>Start working on it</Button>}
            <Button loading={busy} disabled={!resolution.trim()} onClick={() => save({ status: "resolved", resolution, admin_note: note })}>Resolve</Button>
            <Button variant="danger" loading={busy} disabled={!resolution.trim()} onClick={() => save({ status: "dismissed", resolution, admin_note: note })}>Dismiss</Button>
          </div>
        </div>
      )}
    </li>
  );
}

export function AdminIssuesPage() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState("open_work");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), ...(status ? { status } : {}), ...(q.trim() ? { q: q.trim() } : {}) });
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-issues", status, q, page], queryFn: () => api<Paged<AdminIssueRow>>(`/admin/issues?${params}`), refetchInterval: 30_000 });
  return (
    <Card title="Reported problems">
      <div className="mb-4 grid gap-3 sm:grid-cols-2">
        <Field id="iq" label="Search" placeholder="Booking reference, person or words in the report" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <Select id="istatus" label="Show" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="open_work">Needs work (open and in progress)</option><option value="open">Open</option><option value="in_progress">In progress</option><option value="resolved">Resolved</option><option value="dismissed">Dismissed</option><option value="">All</option>
        </Select>
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <ul className="space-y-3">{data?.items.map((i) => <IssueCard key={`${i.id}-${i.status}-${i.blocks_payout}`} i={i} onDone={() => queryClient.invalidateQueries({ queryKey: ["admin-issues"] })} />)}</ul>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">Nothing here.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}
