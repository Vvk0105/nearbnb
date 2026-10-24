import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import type { User } from "../lib/types";
import { Icon, type IconName } from "./icons";
import { useDismiss } from "./useDismiss";

type Entry = { to: string; label: string; icon: IconName };

/** The profile menu in the top bar: everything about "me" in one place, so the bar itself stays clean. */
export function UserMenu({ user, hostLabel, onLogout }: { user: User; hostLabel: string; onLogout: () => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const location = useLocation();
  useEffect(() => setOpen(false), [location.pathname]);

  useDismiss(open, () => setOpen(false), box);

  const entries: Entry[] = user.is_admin
    ? [{ to: "/admin", label: "Admin dashboard", icon: "dashboard" }, { to: "/account", label: "Account settings", icon: "settings" }]
    : [
        { to: "/account/trips", label: "My trips", icon: "trip" },
        { to: "/host", label: hostLabel, icon: "host" },
        { to: "/account", label: "Account settings", icon: "settings" },
      ];

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-label="Account menu"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-3 text-sm transition hover:shadow-sm"
      >
        <span aria-hidden="true" className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-600 text-sm font-semibold text-white">{user.full_name.slice(0, 1).toUpperCase()}</span>
        <span className="hidden max-w-[10rem] truncate font-medium sm:inline">{user.full_name}</span>
        <Icon name="arrow" className={`h-3.5 w-3.5 text-slate-400 transition ${open ? "-rotate-90" : "rotate-90"}`} />
      </button>
      {open && (
        <div role="menu" aria-label="Account" className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-2xl border border-slate-200 bg-white py-1 shadow-xl">
          <div className="border-b border-slate-100 px-4 py-3">
            <p className="truncate text-sm font-semibold">{user.full_name}</p>
            <p className="truncate text-xs text-slate-500">{user.email}</p>
          </div>
          <div className="py-1">
            {entries.map((e) => (
              <Link key={e.to} to={e.to} role="menuitem" className="flex items-center gap-3 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50">
                <Icon name={e.icon} className="h-[18px] w-[18px] text-slate-400" /> {e.label}
              </Link>
            ))}
          </div>
          <div className="border-t border-slate-100 py-1">
            <button type="button" role="menuitem" onClick={onLogout} className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-slate-700 hover:bg-slate-50">
              <Icon name="logout" className="h-[18px] w-[18px] text-slate-400" /> Log out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
