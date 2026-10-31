import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { HostInfo, User } from "../lib/types";
import { Logo } from "./Logo";
import { NotificationBell } from "./NotificationBell";
import { UserMenu } from "./UserMenu";
import { Alert, Button, errorMessage } from "./ui";

/** The word for the host area in the profile menu, by how far the person has got. */
function hostLinkText(state: string | undefined): string {
  if (state === "none") return "Become a host";
  if (["draft", "changes_requested", "pending_review", "rejected", "suspended"].includes(state ?? "")) return "My application";
  return "Host";
}

/** The public site frame (header, footer). Layout puts the routed page inside it; the host area reuses it for people who are not hosts yet. */
export function PublicFrame({ children }: { children: ReactNode }) {
  const { user, logout, loading } = useAuth();
  const { data: host } = useQuery({
    queryKey: ["host"], queryFn: () => api<HostInfo>("/host"), enabled: !!user && !user.is_admin, retry: false, staleTime: 60_000,
  });
  const navigate = useNavigate();
  const [logoutError, setLogoutError] = useState("");
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-1.5 text-sm transition ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"}`;

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-ink">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur print:hidden">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <Logo />
          <nav className="flex items-center gap-1">
            {loading ? (
              // Still checking who this is: show nothing rather than a "Log in" link that would be wrong a moment later.
              <span aria-hidden="true" className="h-9 w-32" />
            ) : user ? (
              <>
              <NotificationBell />
              <UserMenu
                user={user}
                hostLabel={hostLinkText(host?.state)}
                onLogout={async () => {
                  setLogoutError("");
                  try {
                    await logout();
                    navigate("/login");
                  } catch (e) {
                    setLogoutError(errorMessage(e));
                  }
                }}
              />
              </>
            ) : (
              <>
                <NavLink to="/login" className={link}>
                  Log in
                </NavLink>
                <NavLink to="/register" className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700">
                  Sign up
                </NavLink>
              </>
            )}
          </nav>
        </div>
      </header>
      {user && user.pending_consents.length > 0 && <ConsentGate user={user} />}
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">
        {logoutError && (
          <div className="mb-4">
            <Alert>Could not log out: {logoutError}</Alert>
          </div>
        )}
        {children}
      </main>
      <footer className="border-t border-slate-200 bg-white print:hidden">
        <div className="mx-auto grid max-w-6xl gap-8 px-4 py-10 text-sm text-slate-600 sm:grid-cols-3">
          <div>
            <Logo className="!text-xl" />
            <p className="mt-2 max-w-xs text-slate-500">Book a place for exactly the hours you need. Real availability, no surprises.</p>
          </div>
          <div>
            <p className="mb-2 font-semibold text-slate-800">Guests</p>
            <ul className="space-y-1.5">
              <li><Link to="/search" className="hover:text-brand-700 hover:underline">Find a place</Link></li>
              <li><Link to="/account/trips" className="hover:text-brand-700 hover:underline">Your trips</Link></li>
            </ul>
          </div>
          <div>
            <p className="mb-2 font-semibold text-slate-800">Hosting and legal</p>
            <ul className="space-y-1.5">
              <li><Link to="/host" className="hover:text-brand-700 hover:underline">Become a host</Link></li>
              <li><Link to="/legal/terms" className="hover:text-brand-700 hover:underline">Terms</Link></li>
              <li><Link to="/legal/privacy" className="hover:text-brand-700 hover:underline">Privacy</Link></li>
              <li><Link to="/delete-account" className="hover:text-brand-700 hover:underline">Delete your account</Link></li>
            </ul>
          </div>
        </div>
        <p className="border-t border-slate-100 py-4 text-center text-xs text-slate-400">© {new Date().getFullYear()} NearBnB</p>
      </footer>
    </div>
  );
}

export function Layout() {
  return (
    <PublicFrame>
      <Outlet />
    </PublicFrame>
  );
}

/** Blocks the app until the user accepts newly published Terms / Privacy versions. */
export function ConsentGate({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const accept = async () => {
    setBusy(true);
    try {
      let latest: User = user;
      for (const c of user.pending_consents) {
        latest = await api<User>("/me/consents", { method: "POST", body: { doc_type: c.doc_type } });
      }
      setUser(latest);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md space-y-4 rounded-xl bg-white p-6 shadow-xl">
        <h2 className="text-lg font-semibold">We updated our policies</h2>
        <p className="text-sm text-gray-600">Please review and accept to continue:</p>
        <ul className="list-disc pl-5 text-sm">
          {user.pending_consents.map((c) => (
            <li key={c.doc_type}>
              <Link to={`/legal/${c.doc_type}`} target="_blank" className="text-brand-600 underline">
                {c.title}
              </Link>
            </li>
          ))}
        </ul>
        {error && <Alert>{error}</Alert>}
        <Button onClick={accept} loading={busy}>
          I accept
        </Button>
      </div>
    </div>
  );
}

export function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { user, loading } = useAuth();
  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (!user?.is_admin) return <Navigate to="/" replace />;
  return <Outlet />;
}
