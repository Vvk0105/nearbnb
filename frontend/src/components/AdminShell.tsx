import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AdminDashboardData } from "../lib/types";
import { ConsentGate } from "./Layout";
import { Icon, type IconName } from "./icons";
import { Logo } from "./Logo";

type Item = { to: string; label: string; icon: IconName; end?: boolean; badge?: string[] };
type Group = { title: string; items: Item[] };

// Badge keys are alert keys from the dashboard: the number shown is how many things in that area need a person.
const GROUPS: Group[] = [
  { title: "Overview", items: [{ to: "/admin", label: "Dashboard", icon: "dashboard", end: true }] },
  { title: "People", items: [
    { to: "/admin/hosts", label: "Hosts", icon: "host", badge: ["applications", "host_cancellations"] },
    { to: "/admin/users", label: "Users", icon: "users" },
  ] },
  { title: "Listings", items: [
    { to: "/admin/listings", label: "Listings", icon: "home", badge: ["listings", "revisions"] },
    { to: "/admin/catalogues", label: "Types & amenities", icon: "tag" },
  ] },
  { title: "Bookings & money", items: [
    { to: "/admin/bookings", label: "Bookings", icon: "booking" },
    { to: "/admin/payments", label: "Payments", icon: "card", badge: ["failed_payments"] },
    { to: "/admin/payouts", label: "Payouts", icon: "wallet", badge: ["failed_payouts"] },
    { to: "/admin/refunds", label: "Refunds", icon: "refund", badge: ["refund_requests", "failed_refunds"] },
    { to: "/admin/cancellation", label: "Cancellation", icon: "flag" },
    { to: "/admin/tax", label: "Tax", icon: "percent" },
    { to: "/admin/exports", label: "Exports", icon: "download" },
  ] },
  { title: "Quality", items: [
    { to: "/admin/issues", label: "Issues", icon: "alert", badge: ["open_issues"] },
    { to: "/admin/reviews", label: "Reviews", icon: "star" },
    { to: "/admin/calendar", label: "Calendar", icon: "calendar", badge: ["ical_conflicts"] },
  ] },
  { title: "System", items: [
    { to: "/admin/settings", label: "Settings", icon: "settings" },
    { to: "/admin/legal", label: "Legal documents", icon: "doc" },
    { to: "/admin/audit", label: "Audit log", icon: "list" },
    { to: "/admin/webhooks", label: "Webhooks", icon: "bolt", badge: ["failed_webhooks"] },
  ] },
];

const ALL_ITEMS = GROUPS.flatMap((g) => g.items);

/** The name of the page the admin is on, for the top bar. Longest matching address wins. */
export function pageTitle(pathname: string): string {
  const hit = [...ALL_ITEMS].sort((a, b) => b.to.length - a.to.length).find((i) => (i.end ? pathname === i.to : pathname === i.to || pathname.startsWith(`${i.to}/`)));
  return hit?.label ?? "Admin";
}

function Nav({ counts, onNavigate }: { counts: Record<string, number>; onNavigate?: () => void }) {
  return (
    <nav aria-label="Admin" className="space-y-5 px-3 pb-6">
      {GROUPS.map((g) => (
        <div key={g.title}>
          <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{g.title}</p>
          <ul className="space-y-0.5">
            {g.items.map((item) => {
              const n = (item.badge ?? []).reduce((sum, k) => sum + (counts[k] ?? 0), 0);
              return (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    onClick={onNavigate}
                    className={({ isActive }) =>
                      `group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${isActive ? "bg-brand-500/15 font-medium text-white" : "text-slate-300 hover:bg-white/5 hover:text-white"}`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <Icon name={item.icon} className={`h-[18px] w-[18px] shrink-0 ${isActive ? "text-brand-300" : "text-slate-400 group-hover:text-slate-200"}`} />
                        <span className="flex-1">{item.label}</span>
                        {n > 0 && <span aria-label={`${n} need attention`} className="rounded-full bg-amber-400 px-2 py-0.5 text-[11px] font-semibold text-slate-900">{n}</span>}
                      </>
                    )}
                  </NavLink>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

/** Admin area shell: dark sidebar with grouped navigation and live counts, a top bar, and the page below. */
export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [location.pathname]);

  const { data } = useQuery({ queryKey: ["admin-dashboard"], queryFn: () => api<AdminDashboardData>("/admin/dashboard"), refetchInterval: 60_000 });
  const counts: Record<string, number> = Object.fromEntries((data?.alerts ?? []).filter((a) => a.severity !== "info").map((a) => [a.key, a.count]));
  const title = pageTitle(location.pathname);

  const sidebar = (
    <div className="flex h-full flex-col bg-slate-900">
      <div className="flex h-16 shrink-0 items-center justify-between px-5">
        <Link to="/admin" aria-label="Admin home" className="text-xl font-extrabold tracking-tight text-white">
          Near<span className="text-brand-400">BnB</span> <span className="ml-1 rounded bg-white/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-300">Admin</span>
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto pt-2">
        <Nav counts={counts} onNavigate={() => setOpen(false)} />
      </div>
      <div className="border-t border-white/10 p-3">
        <Link to="/" className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-slate-300 hover:bg-white/5 hover:text-white">
          <Icon name="external" className="h-[18px] w-[18px] text-slate-400" /> View the website
        </Link>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-100 text-ink">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block print:hidden">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Admin menu">
          <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85%] shadow-xl">{sidebar}</div>
        </div>
      )}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-slate-200 bg-white/90 px-4 backdrop-blur sm:px-6 print:hidden">
          <button type="button" aria-label="Open menu" className="rounded-lg p-2 text-slate-600 hover:bg-slate-100 lg:hidden" onClick={() => setOpen(true)}>
            <Icon name="menu" />
          </button>
          <div className="lg:hidden"><Logo className="!text-lg" /></div>
          <h1 className="hidden text-lg font-semibold lg:block">{title}</h1>
          <div className="ml-auto flex items-center gap-3">
            <span className="hidden text-sm text-slate-500 sm:inline">{user?.full_name}</span>
            <span aria-hidden="true" className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-sm font-semibold text-white">{(user?.full_name ?? "A").slice(0, 1).toUpperCase()}</span>
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
              onClick={async () => {
                await logout();
                navigate("/login");
              }}
            >
              <Icon name="logout" className="h-4 w-4" /> Log out
            </button>
          </div>
        </header>
        {user && user.pending_consents.length > 0 && <ConsentGate user={user} />}
        <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
          <h1 className="text-xl font-semibold lg:hidden">{title}</h1>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
