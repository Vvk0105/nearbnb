import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
import { api } from "../lib/api";
import type { AdminDashboardData } from "../lib/types";
import { AppShell, titleFor, type Group } from "./AppShell";

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

/** The name of the page the admin is on, for the top bar. */
export function pageTitle(pathname: string): string {
  return titleFor(GROUPS, pathname, "Admin");
}

/** Admin area: sidebar with live counts, top bar, and the page below. */
export function AdminLayout() {
  const location = useLocation();
  const { data } = useQuery({ queryKey: ["admin-dashboard"], queryFn: () => api<AdminDashboardData>("/admin/dashboard"), refetchInterval: 60_000 });
  const counts: Record<string, number> = Object.fromEntries((data?.alerts ?? []).filter((a) => a.severity !== "info").map((a) => [a.key, a.count]));
  return <AppShell area="Admin" groups={GROUPS} counts={counts} homeTo="/admin" title={pageTitle(location.pathname)} />;
}
