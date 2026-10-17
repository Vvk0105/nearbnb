import { useState } from "react";
import { Link, NavLink, Outlet, Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { User } from "../lib/types";
import { Logo } from "./Logo";
import { Alert, Button, errorMessage } from "./ui";

export function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [logoutError, setLogoutError] = useState("");
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded px-2 py-1 text-sm ${isActive ? "bg-brand-50 font-medium text-brand-700" : "text-gray-600 hover:text-gray-900"}`;

  return (
    <div className="min-h-screen bg-slate-50 text-ink">
      <header className="border-b border-gray-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <Logo />
          <nav className="flex items-center gap-2">
            <NavLink to="/search" className={link}>
              Explore
            </NavLink>
            {user?.is_admin && (
              <NavLink to="/admin/hosts" className={link}>
                Admin
              </NavLink>
            )}
            {user && !user.is_admin && (
              <NavLink to="/host" className={link}>
                Host
              </NavLink>
            )}
            {user ? (
              <>
                <NavLink to="/account" className={link}>
                  {user.full_name}
                </NavLink>
                <Button
                  variant="secondary"
                  onClick={async () => {
                    setLogoutError("");
                    try {
                      await logout();
                      navigate("/login");
                    } catch (e) {
                      setLogoutError(errorMessage(e));
                    }
                  }}
                >
                  Log out
                </Button>
              </>
            ) : (
              <>
                <NavLink to="/login" className={link}>
                  Log in
                </NavLink>
                <NavLink to="/register" className={link}>
                  Sign up
                </NavLink>
              </>
            )}
          </nav>
        </div>
      </header>
      {user && user.pending_consents.length > 0 && <ConsentGate user={user} />}
      <main className="mx-auto max-w-5xl px-4 py-8">
        {logoutError && (
          <div className="mb-4">
            <Alert>Could not log out: {logoutError}</Alert>
          </div>
        )}
        <Outlet />
      </main>
      <footer className="mx-auto max-w-5xl px-4 py-6 text-xs text-gray-500">
        <Link to="/legal/terms" className="mr-4 hover:underline">
          Terms
        </Link>
        <Link to="/legal/privacy" className="hover:underline">
          Privacy
        </Link>
      </footer>
    </div>
  );
}

/** Blocks the app until the user accepts newly published Terms / Privacy versions. */
function ConsentGate({ user }: { user: User }) {
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
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  return <Outlet />;
}

export function RequireAdmin() {
  const { user, loading } = useAuth();
  if (loading) return <p className="text-gray-500">Loading...</p>;
  if (!user?.is_admin) return <Navigate to="/" replace />;
  return <Outlet />;
}
