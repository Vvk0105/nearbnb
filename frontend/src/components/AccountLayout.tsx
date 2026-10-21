import { NavLink, Outlet } from "react-router-dom";
import { Icon, type IconName } from "./icons";

const ITEMS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/account", label: "Profile", icon: "users", end: true },
  { to: "/account/security", label: "Security", icon: "shield" },
];

/** Account settings only (profile and security): a side menu (tabs on a phone) and the chosen section beside it. Trips are bookings, not settings, so they live on their own page. */
export function AccountLayout() {
  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_1fr]">
      <nav aria-label="Account settings" className="lg:sticky lg:top-24 lg:self-start">
        <p className="mb-2 hidden px-3 text-xs font-semibold uppercase tracking-wider text-slate-400 lg:block">Account settings</p>
        <ul className="flex gap-1 overflow-x-auto lg:flex-col">
          {ITEMS.map((i) => (
            <li key={i.to} className="shrink-0">
              <NavLink
                to={i.to}
                end={i.end}
                className={({ isActive }) =>
                  `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"}`
                }
              >
                <Icon name={i.icon} className="h-[18px] w-[18px]" /> {i.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 max-w-3xl">
        <Outlet />
      </div>
    </div>
  );
}
