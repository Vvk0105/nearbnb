import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { User } from "../../lib/types";
import { Alert, Button, Field, errorMessage } from "../../components/ui";
import { Icon } from "../../components/icons";
import { Turnstile } from "../../components/Turnstile";

const PERKS = [
  "Check in at the hour that suits you",
  "See real availability before you ask",
  "Secure card payments through Stripe",
  "Honest, two-sided reviews",
];

/** Login-style pages: a short pitch on the left (large screens) and the form on the right. */
function Shell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mx-auto grid max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm lg:grid-cols-2">
      <aside aria-hidden="true" className="relative hidden flex-col justify-between bg-gradient-to-br from-slate-900 via-brand-900 to-brand-700 p-10 text-white lg:flex">
        <div>
          <p className="text-2xl font-extrabold tracking-tight">Near<span className="text-brand-300">BnB</span></p>
          <p className="mt-6 text-3xl font-bold leading-tight">Stay exactly as long as you need.</p>
        </div>
        <ul className="space-y-3 text-sm text-brand-50">
          {PERKS.map((p) => (
            <li key={p} className="flex items-center gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/15"><Icon name="check" className="h-3.5 w-3.5" /></span>
              {p}
            </li>
          ))}
        </ul>
      </aside>
      <div className="p-6 sm:p-10">
        <h1 className="mb-6 text-2xl font-bold tracking-tight">{title}</h1>
        {children}
      </div>
    </div>
  );
}

export function LoginPage() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [captcha, setCaptcha] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const user = await api<User>("/auth/login", {
        method: "POST",
        body: { email, password, totp_code: needTotp ? totp : undefined, turnstile_token: captcha },
      });
      setUser(user);
      navigate((location.state as { from?: string } | null)?.from ?? "/account", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === "TOTP_REQUIRED") {
        setNeedTotp(true);
      } else if (err instanceof ApiError && err.code === "EMAIL_NOT_VERIFIED") {
        navigate(`/verify?email=${encodeURIComponent(email)}`);
      } else {
        setError(errorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="Log in">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Field id="email" label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field id="password" label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {needTotp && (
          <Field id="totp" label="Authentication code" inputMode="numeric" maxLength={6} autoFocus required value={totp} onChange={(e) => setTotp(e.target.value)} hint="From your authenticator app" />
        )}
        <Turnstile onToken={setCaptcha} />
        <Button type="submit" loading={busy} className="w-full">
          Log in
        </Button>
        <p className="text-sm">
          <Link to="/forgot-password" className="text-brand-600 hover:underline">
            Forgot password?
          </Link>
          {" · "}
          <Link to="/register" className="text-brand-600 hover:underline">
            Create account
          </Link>
        </p>
      </form>
    </Shell>
  );
}

export function RegisterPage() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [accepted, setAccepted] = useState(false);
  const [captcha, setCaptcha] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/auth/register", { method: "POST", body: { ...form, accept_terms: accepted, turnstile_token: captcha } });
      navigate(`/verify?email=${encodeURIComponent(form.email)}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="Create your account">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Field id="name" label="Full name" autoComplete="name" required value={form.full_name} onChange={set("full_name")} />
        <Field id="email" label="Email" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        <Field id="password" label="Password" type="password" autoComplete="new-password" minLength={10} required value={form.password} onChange={set("password")} hint="At least 10 characters, with a lowercase letter, an uppercase letter and a number" />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} required />
          <span>
            I agree to the{" "}
            <Link to="/legal/terms" target="_blank" className="text-brand-600 underline">
              Terms of Service
            </Link>{" "}
            and{" "}
            <Link to="/legal/privacy" target="_blank" className="text-brand-600 underline">
              Privacy Policy
            </Link>
          </span>
        </label>
        <Turnstile onToken={setCaptcha} />
        <Button type="submit" loading={busy} disabled={!accepted} className="w-full">
          Create account
        </Button>
        <p className="text-sm">
          Already have an account?{" "}
          <Link to="/login" className="text-brand-600 hover:underline">
            Log in
          </Link>
        </p>
      </form>
    </Shell>
  );
}

export function VerifyEmailPage() {
  const [params] = useSearchParams();
  const email = params.get("email") ?? "";
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const user = await api<User>("/auth/verify-email", { method: "POST", body: { email, code } });
      setUser(user);
      navigate("/account", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setError("");
    setInfo("");
    try {
      await api("/auth/resend-otp", { method: "POST", body: { email } });
      setInfo("A new code has been sent.");
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Shell title="Verify your email">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-gray-600">
          We sent a 6-digit code to <strong>{email || "your email"}</strong>. It expires in 10 minutes.
        </p>
        {error && <Alert>{error}</Alert>}
        {info && <Alert kind="success">{info}</Alert>}
        <Field id="code" label="Verification code" inputMode="numeric" maxLength={6} autoFocus required value={code} onChange={(e) => setCode(e.target.value)} />
        <div className="flex items-center gap-3">
          <Button type="submit" loading={busy}>
            Verify
          </Button>
          <Button type="button" variant="secondary" onClick={resend} disabled={!email}>
            Resend code
          </Button>
        </div>
      </form>
    </Shell>
  );
}

export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [captcha, setCaptcha] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/auth/forgot-password", { method: "POST", body: { email, turnstile_token: captcha } });
      navigate(`/reset-password?email=${encodeURIComponent(email)}`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="Forgot your password?">
      <form onSubmit={submit} className="space-y-4">
        <p className="text-sm text-gray-600">Enter your email and we will send you a reset code.</p>
        {error && <Alert>{error}</Alert>}
        <Field id="email" label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Turnstile onToken={setCaptcha} />
        <Button type="submit" loading={busy}>
          Send code
        </Button>
      </form>
    </Shell>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api("/auth/reset-password", { method: "POST", body: { email, code, new_password: password } });
      navigate("/login", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell title="Choose a new password">
      <form onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <Field id="email" label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field id="code" label="Reset code" inputMode="numeric" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} />
        <Field id="password" label="New password" type="password" autoComplete="new-password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} hint="At least 10 characters, with a lowercase letter, an uppercase letter and a number" />
        <Button type="submit" loading={busy}>
          Reset password
        </Button>
      </form>
    </Shell>
  );
}
