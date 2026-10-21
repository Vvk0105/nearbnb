import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { money } from "../../lib/format";
import type { AdminUserDetail, AdminUserRow, Paged } from "../../lib/types";
import { BookingBadge } from "../../components/bookings";
import { Pager } from "../../components/Pager";
import { Alert, Button, Card, Field, Select, StatusBadge, TextArea, errorMessage } from "../../components/ui";

export function AdminUsersPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const params = new URLSearchParams({ page: String(page), ...(q.trim() ? { q: q.trim() } : {}), ...(status ? { status } : {}), ...(role ? { role } : {}) });
  const { data, error, isLoading } = useQuery({ queryKey: ["admin-users", q, status, role, page], queryFn: () => api<Paged<AdminUserRow>>(`/admin/users?${params}`) });
  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setPage(1); };
  return (
    <Card title="Users">
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Field id="uq" label="Search" placeholder="Name, email or phone" value={q} onChange={(e) => reset(setQ)(e.target.value)} />
        <Select id="ustatus" label="Status" value={status} onChange={(e) => reset(setStatus)(e.target.value)}>
          <option value="">Any</option><option value="active">Active</option><option value="suspended">Suspended</option><option value="unverified">Not verified</option><option value="deactivated">Deactivated</option>
        </Select>
        <Select id="urole" label="Role" value={role} onChange={(e) => reset(setRole)(e.target.value)}>
          <option value="">Anyone</option><option value="guest">Guests only</option><option value="host">Hosts</option><option value="admin">Admins</option>
        </Select>
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-gray-500">Loading...</p>}
      <div className="overflow-x-auto">
        <div className="overflow-x-auto"><table className="data-table">
          <thead className="text-xs uppercase text-gray-500"><tr><th className="py-2 pr-3">Name</th><th className="pr-3">Email</th><th className="pr-3">Role</th><th className="pr-3">Status</th><th className="pr-3">Bookings</th><th>Last login</th></tr></thead>
          <tbody>
            {data?.items.map((u) => (
              <tr key={u.id} className="border-t border-gray-100">
                <td className="py-2 pr-3"><Link className="text-brand-700 underline" to={`/admin/users/${u.id}`}>{u.full_name}</Link></td>
                <td className="pr-3">{u.email}</td>
                <td className="pr-3">{u.is_admin ? "Admin" : u.is_host ? `Host (${u.host_state})` : "Guest"}</td>
                <td className="pr-3"><StatusBadge status={u.status} /></td>
                <td className="pr-3">{u.bookings}</td>
                <td className="text-xs text-gray-500">{u.last_login_at?.slice(0, 10) ?? "never"}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
      {data && data.items.length === 0 && <p className="py-6 text-center text-sm text-gray-500">No users match.</p>}
      {data && <Pager page={page} pageSize={data.page_size} total={data.total} onChange={setPage} />}
    </Card>
  );
}

export function AdminUserDetailPage() {
  const { userId } = useParams();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data, error: loadError, isLoading } = useQuery({ queryKey: ["admin-user", userId], queryFn: () => api<AdminUserDetail>(`/admin/users/${userId}`) });
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (loadError || !data) return <Alert>{errorMessage(loadError)}</Alert>;

  const act = async (path: string) => {
    setBusy(true);
    setError("");
    try {
      const fresh = await api<AdminUserDetail>(`/admin/users/${userId}/${path}`, { method: "POST", body: { reason } });
      queryClient.setQueryData(["admin-user", userId], fresh);
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
      setReason("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Link to="/admin/users" className="text-sm text-brand-700 hover:underline">← Users</Link>
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-bold">{data.full_name}</h1>
        <StatusBadge status={data.status} />
        {data.is_admin && <span className="rounded-full bg-gray-800 px-2 py-0.5 text-xs text-white">Admin</span>}
        {data.host_id && <Link className="text-sm text-brand-700 underline" to={`/admin/hosts/${data.host_id}`}>Host account ({data.host_state})</Link>}
      </div>
      <Card title="Profile">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-gray-500">Email</dt><dd>{data.email} {data.email_verified ? <span className="text-green-700">✓ verified</span> : <span className="text-amber-700">not verified</span>}</dd></div>
          <div><dt className="text-xs text-gray-500">Phone</dt><dd>{data.phone ?? "-"}</dd></div>
          <div><dt className="text-xs text-gray-500">Date of birth</dt><dd>{data.dob ?? "-"}</dd></div>
          <div><dt className="text-xs text-gray-500">Joined / last login</dt><dd>{data.created_at?.slice(0, 10)} / {data.last_login_at?.slice(0, 10) ?? "never"}</dd></div>
          <div className="sm:col-span-2"><dt className="text-xs text-gray-500">Social profiles</dt><dd>{data.social_links.map((s) => <a key={s.url} className="mr-3 text-brand-700 underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.platform}</a>)}{data.social_links.length === 0 && "-"}</dd></div>
        </dl>
      </Card>

      {!data.is_admin && (
        <Card title="Account access">
          {error && <div className="mb-3"><Alert>{error}</Alert></div>}
          {data.status === "suspended" ? (
            <div className="space-y-3">
              <Alert>This account is suspended. The user cannot log in.</Alert>
              <Field id="ureason" label="Note (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <Button loading={busy} onClick={() => act("reactivate")}>Reactivate</Button>
            </div>
          ) : data.status === "active" ? (
            <div className="space-y-3">
              <p className="text-sm text-gray-600">Suspending signs the user out everywhere and stops them logging in. They are emailed the reason. Existing bookings are not cancelled automatically.</p>
              <TextArea id="sreason" label="Reason (required, recorded and emailed)" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
              <Button variant="danger" loading={busy} disabled={!reason.trim()} onClick={() => act("suspend")}>Suspend user</Button>
            </div>
          ) : <p className="text-sm text-gray-600">This account is {data.status}, so there is nothing to change here.</p>}
        </Card>
      )}

      <Card title="Recent bookings as a guest">
        <ul className="space-y-2 text-sm">
          {data.recent_bookings.map((b) => (
            <li key={b.id} className="flex flex-wrap items-center justify-between gap-2">
              <span><Link className="font-mono text-xs text-brand-700 underline" to={`/admin/bookings/${b.id}`}>{b.reference}</Link> · {b.property.name} · {b.check_in_label} · {money(b.price.total ?? 0, b.currency)}</span>
              <BookingBadge status={b.status} as="host" />
            </li>
          ))}
          {data.recent_bookings.length === 0 && <li className="text-gray-500">No bookings.</li>}
        </ul>
      </Card>

      <Card title="History">
        <ul className="space-y-1 text-sm">
          {data.history.map((h, i) => (
            <li key={i} className="flex flex-wrap justify-between gap-2 border-b border-gray-100 py-1 last:border-0">
              <span><code className="text-xs">{h.action}</code>{h.actor && <span className="text-gray-500"> by {h.actor}</span>}{h.reason && <span className="text-gray-500"> · {h.reason}</span>}</span>
              <span className="text-xs text-gray-400">{h.at.slice(0, 16).replace("T", " ")}</span>
            </li>
          ))}
          {data.history.length === 0 && <li className="text-gray-500">Nothing recorded.</li>}
        </ul>
      </Card>
    </div>
  );
}
