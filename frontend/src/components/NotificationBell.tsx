import { useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { timeAgo } from "../lib/format";
import type { Notice, NoticeList } from "../lib/types";
import { Icon } from "./icons";
import { useDismiss } from "./useDismiss";

const DOT: Record<string, string> = { action: "bg-amber-500", problem: "bg-red-500", news: "bg-slate-300" };
export const TONE_LABEL: Record<string, string> = { action: "Needs you", problem: "Problem", news: "" };

/** Keeps the list and the number in step after something is read. */
export function useNotificationActions() {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["notifications"] });
  const readOne = useMutation({ mutationFn: (id: string) => api(`/me/notifications/${id}/read`, { method: "POST" }), onSuccess: refresh });
  const readAll = useMutation({ mutationFn: () => api("/me/notifications/read-all", { method: "POST" }), onSuccess: refresh });
  return { readOne, readAll };
}

export function NoticeRow({ n, onOpen }: { n: Notice; onOpen: (n: Notice) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(n)}
        className={`flex w-full gap-3 px-4 py-3 text-left transition hover:bg-slate-50 ${n.read ? "" : "bg-brand-50/50"}`}
      >
        <span aria-hidden="true" className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${n.read ? "bg-transparent" : DOT[n.tone] ?? DOT.news}`} />
        <span className="min-w-0 flex-1">
          <span className={`block text-sm ${n.read ? "text-slate-600" : "font-semibold text-slate-900"}`}>
            {!n.read && <span className="sr-only">Unread. </span>}
            {n.title}
          </span>
          {n.body && <span className="mt-0.5 block text-xs text-slate-600">{n.body}</span>}
          <span className="mt-1 flex items-center gap-2 text-xs text-slate-400">
            {timeAgo(n.created_at)}
            {TONE_LABEL[n.tone] && <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${n.tone === "problem" ? "bg-red-100 text-red-700" : "bg-amber-100 text-amber-800"}`}>{TONE_LABEL[n.tone]}</span>}
          </span>
        </span>
      </button>
    </li>
  );
}

/** The bell in the top bar: a number for what is unread, and the latest messages when it is opened. */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const [seenPath, setSeenPath] = useState(location.pathname);
  if (seenPath !== location.pathname) {
    setSeenPath(location.pathname);
    if (open) setOpen(false);
  }
  useDismiss(open, () => setOpen(false), box);

  const count = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api<{ unread: number }>("/me/notifications/unread-count"),
    refetchInterval: 60_000,
    retry: false,
  });
  const list = useQuery({
    queryKey: ["notifications", "latest"],
    queryFn: () => api<NoticeList>("/me/notifications?page_size=8"),
    enabled: open,
  });
  const { readOne, readAll } = useNotificationActions();

  const unread = count.data?.unread ?? 0;
  const go = (n: Notice) => {
    if (!n.read) readOne.mutate(n.id);
    setOpen(false);
    if (n.link) navigate(n.link);
  };

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : "Notifications"}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="relative rounded-full p-2 text-slate-600 transition hover:bg-slate-100 hover:text-slate-900"
      >
        <Icon name="bell" className="h-5 w-5" />
        {unread > 0 && (
          <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div role="dialog" aria-label="Notifications" className="absolute right-0 z-40 mt-2 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <p className="text-sm font-semibold">Notifications</p>
            {unread > 0 && (
              <button type="button" onClick={() => readAll.mutate()} className="text-xs font-medium text-brand-700 hover:underline">Mark all as read</button>
            )}
          </div>
          <div className="max-h-96 overflow-y-auto">
            {list.isLoading && <p className="px-4 py-6 text-center text-sm text-slate-500">Loading...</p>}
            {list.error && <p className="px-4 py-6 text-center text-sm text-red-700">Could not load your notifications.</p>}
            {list.data && list.data.items.length === 0 && (
              <div className="px-4 py-8 text-center">
                <Icon name="check" className="mx-auto h-6 w-6 text-slate-300" />
                <p className="mt-2 text-sm text-slate-600">You are all caught up.</p>
              </div>
            )}
            {list.data && list.data.items.length > 0 && <ul className="divide-y divide-slate-100">{list.data.items.map((n) => <NoticeRow key={n.id} n={n} onOpen={go} />)}</ul>}
          </div>
          <div className="border-t border-slate-100 px-4 py-2.5 text-center">
            <Link to="/notifications" onClick={() => setOpen(false)} className="text-sm font-medium text-brand-700 hover:underline">See all notifications</Link>
          </div>
        </div>
      )}
    </div>
  );
}
