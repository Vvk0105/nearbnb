import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { IcalConflictRow, IcalSourceRow, Paged } from "../../lib/types";
import { ConflictList } from "../../components/CalendarSync";
import { Alert, Card, errorMessage } from "../../components/ui";

type FailingSource = IcalSourceRow & { property: string; unit: string; failures: number };

/** Calendar clashes and calendars that have stopped syncing, across every host. */
export function AdminCalendarPage() {
  const queryClient = useQueryClient();
  const [msg, setMsg] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const { data, error, isLoading } = useQuery({
    queryKey: ["admin-ical"],
    queryFn: () => api<{ conflicts: Paged<IcalConflictRow>; failing_sources: FailingSource[] }>("/admin/ical?status=open"),
    refetchInterval: 30_000,
  });
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setMsg(null);
    try {
      await fn();
      setMsg({ kind: "success", text: success });
      await queryClient.invalidateQueries({ queryKey: ["admin-ical"] });
    } catch (e) {
      setMsg({ kind: "error", text: errorMessage(e) });
    }
  };
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error || !data) return <Alert>{errorMessage(error)}</Alert>;
  return (
    <div className="space-y-6">
      {msg && <Alert kind={msg.kind}>{msg.text}</Alert>}
      <Card title="Calendar conflicts">
        <p className="mb-3 text-sm text-gray-600">An outside site took dates that already have a NearBnB booking. Nothing is cancelled automatically: the host and you were emailed, and a person decides. A conflict closes by itself when the dates stop clashing.</p>
        {data.conflicts.items.length === 0 ? <p className="text-sm text-gray-500">No open conflicts.</p> : (
          <ConflictList conflicts={data.conflicts.items} onDismiss={(id) => run(() => api(`/admin/ical/conflicts/${id}/dismiss`, { method: "POST", body: {} }), "Marked as dealt with.")} />
        )}
      </Card>
      <Card title="Calendars that are not syncing">
        {data.failing_sources.length === 0 ? <p className="text-sm text-gray-500">Every connected calendar is syncing.</p> : (
          <ul className="space-y-3">
            {data.failing_sources.map((s) => (
              <li key={s.id} className="rounded-lg border border-gray-200 p-3 text-sm">
                <p className="font-medium">{s.property} · {s.unit} · {s.name} <span className="text-xs font-normal text-red-700">failed {s.failures} time{s.failures === 1 ? "" : "s"}</span></p>
                <p className="text-red-800">{s.last_error}</p>
                <button type="button" className="mt-1 text-brand-700 underline" onClick={() => run(() => api(`/admin/ical/sources/${s.id}/sync`, { method: "POST" }), `${s.name} synced.`)}>Try again now</button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
