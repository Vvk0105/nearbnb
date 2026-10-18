import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { api } from "../lib/api";
import type { HostDashboardData } from "../lib/types";
import { useHost } from "../pages/host/HostPages";
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

export function hostPageTitle(pathname: string): string {
  return titleFor(GROUPS, pathname, "Host");
}

/** Host area: the same sidebar layout as admin, with the guest side (trips) one click away. */
export function HostLayout() {
  const location = useLocation();
  const { host } = useHost();
  const active = host?.state === "active";
  const { data } = useQuery({ queryKey: ["host-dashboard"], queryFn: () => api<HostDashboardData>("/host/dashboard"), enabled: active, refetchInterval: 60_000 });
  const counts = { requests: data?.requests.pending ?? 0 };
  return (
    <AppShell
      area="Host"
      groups={GROUPS}
      counts={counts}
      homeTo="/host"
      title={hostPageTitle(location.pathname)}
      titleIsHeading={false}
      footerLinks={[{ to: "/account/trips", label: "My trips (as a guest)", icon: "trip" }, { to: "/account", label: "Account settings", icon: "settings" }]}
    />
  );
}
