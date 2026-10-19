import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation } from "react-router-dom";
import { api } from "../lib/api";
import type { HostDashboardData } from "../lib/types";
import { BecomeHost } from "../pages/host/BecomeHost";
import { useHost } from "../pages/host/HostPages";
import { PublicFrame } from "./Layout";
import { AppShell, titleFor, type Group } from "./AppShell";

const GROUPS: Group[] = [
  { title: "Overview", items: [{ to: "/host", label: "Dashboard", icon: "dashboard", end: true }] },
  { title: "Hosting", items: [
    { to: "/host/requests", label: "Booking requests", icon: "booking", badge: ["requests"] },
    { to: "/host/properties", label: "Properties", icon: "home" },
  ] },
  { title: "Money", items: [
    { to: "/host/money", label: "Earnings", icon: "money" },
    { to: "/host/payouts", label: "Payout setup", icon: "wallet" },
  ] },
  { title: "Account", items: [{ to: "/host/apply", label: "Application", icon: "doc" }] },
];

/**
 * A host only sees what applies to their stage:
 *  - applying (draft, in review, changes requested, rejected, suspended): just the dashboard and the application
 *  - approved or restricted (setting up payouts, drafting listings): plus properties and payout setup
 *  - active: everything, including booking requests and earnings
 */
/** Stages where a person has not been approved to host yet. */
export const APPLYING = new Set(["none", "draft", "changes_requested", "pending_review", "rejected", "suspended"]);
const ACTIVE_ONLY = new Set(["/host/requests", "/host/money"]);
const SETUP_ONLY = new Set(["/host/properties", "/host/payouts"]);
export function groupsFor(state: string | undefined): Group[] {
  const keep = (to: string) => {
    if (state === "active") return true;
    if (state === "approved" || state === "restricted") return !ACTIVE_ONLY.has(to);
    return !ACTIVE_ONLY.has(to) && !SETUP_ONLY.has(to);
  };
  return GROUPS.map((g) => ({ ...g, items: g.items.filter((i) => keep(i.to)) })).filter((g) => g.items.length > 0);
}

export function hostPageTitle(pathname: string): string {
  return titleFor(GROUPS, pathname, "Host");
}

/** Host area: the same sidebar layout as admin, with the guest side (trips) one click away. */
export function HostLayout() {
  const location = useLocation();
  const { host, loading } = useHost();
  const active = host?.state === "active";
  const { data } = useQuery({ queryKey: ["host-dashboard"], queryFn: () => api<HostDashboardData>("/host/dashboard"), enabled: active, refetchInterval: 60_000 });
  const counts = { requests: data?.requests.pending ?? 0 };

  // The host area is for people who can actually host. Someone who has never applied, or is still applying, is
  // on the public site: the pitch or their application page, with no host sidebar and no dashboard.
  if (loading) return <PublicFrame><p className="text-slate-500">Loading...</p></PublicFrame>;
  if (host && APPLYING.has(host.state)) {
    return <PublicFrame>{location.pathname === "/host" && host.state === "none" ? <BecomeHost /> : <div className="mx-auto max-w-3xl"><Outlet /></div>}</PublicFrame>;
  }
  return (
    <AppShell
      area="Host"
      groups={groupsFor(host?.state)}
      counts={counts}
      homeTo="/host"
      title={hostPageTitle(location.pathname)}
      titleIsHeading={false}
      footerLinks={[{ to: "/account/trips", label: "My trips (as a guest)", icon: "trip" }, { to: "/account", label: "Account settings", icon: "settings" }]}
    />
  );
}
