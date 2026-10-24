import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { Notice, NoticeList } from "../lib/types";
import { Alert, Button, errorMessage } from "../components/ui";
import { NoticeRow, useNotificationActions } from "../components/NotificationBell";
import { EmptyState } from "../components/dashboardUi";
import { Pager } from "../components/Pager";

const PAGE_SIZE = 20;

/** Every notification, newest first, with a switch to show only what is unread. */
export function NotificationsPage() {
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const navigate = useNavigate();
  const { readOne, readAll } = useNotificationActions();
  const { data, error, isLoading } = useQuery({
    queryKey: ["notifications", "page", unreadOnly, page],
    queryFn: () => api<NoticeList>(`/me/notifications?page=${page}&page_size=${PAGE_SIZE}${unreadOnly ? "&unread=true" : ""}`),
    placeholderData: keepPreviousData,
  });
  const open = (n: Notice) => {
    if (!n.read) readOne.mutate(n.id);
    if (n.link) navigate(n.link);
  };
  const choose = (only: boolean) => {
    setUnreadOnly(only);
    setPage(1);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold tracking-tight">Notifications</h1>
        {data && data.unread > 0 && <Button variant="secondary" onClick={() => readAll.mutate()} loading={readAll.isPending}>Mark all as read ({data.unread})</Button>}
      </div>
      <div className="flex gap-2" role="tablist">
        {([[false, "All"], [true, "Unread"]] as const).map(([only, label]) => (
          <button key={label} role="tab" aria-selected={unreadOnly === only} onClick={() => choose(only)}
            className={`rounded-full border px-3.5 py-1.5 text-sm transition ${unreadOnly === only ? "border-brand-600 bg-brand-600 text-white" : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"}`}>
            {label}
          </button>
        ))}
      </div>
      {error && <Alert>{errorMessage(error)}</Alert>}
      {isLoading && <p className="text-slate-500">Loading...</p>}
      {data && data.items.length === 0 && (
        <EmptyState icon="bell" title={unreadOnly ? "Nothing unread" : "No notifications yet"}>
          {unreadOnly ? "You are all caught up." : "Updates about your bookings, listings and payments will appear here."}
        </EmptyState>
      )}
      {data && data.items.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <ul className="divide-y divide-slate-100">{data.items.map((n) => <NoticeRow key={n.id} n={n} onOpen={open} />)}</ul>
        </div>
      )}
      {data && <Pager page={page} pageSize={PAGE_SIZE} total={data.total} onChange={setPage} />}
    </div>
  );
}
