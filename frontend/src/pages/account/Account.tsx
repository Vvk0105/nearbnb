import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, mediaUrl } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { SessionInfo, User } from "../../lib/types";
import { Alert, Button, Card, Field, errorMessage } from "../../components/ui";

function useAction() {
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<string | void>) => {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      const msg = await fn();
      if (msg) setInfo(msg);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const banner = (
    <>
      {error && <Alert>{error}</Alert>}
      {info && <Alert kind="success">{info}</Alert>}
    </>
  );
  return { run, busy, banner };
}

function ProfileCard({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [name, setName] = useState(user.full_name);
  const [phone, setPhone] = useState(user.phone ?? "");
  const [dob, setDob] = useState(user.dob ?? "");
  const { run, busy, banner } = useAction();

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      setUser(await api<User>("/me", { method: "PATCH", body: { full_name: name, phone, dob: dob || null } }));
      return "Profile saved.";
    });
  };
  const upload = (file: File | undefined) => {
    if (!file) return;
    void run(async () => {
      const form = new FormData();
      form.append("file", file);
      setUser(await api<User>("/me/photo", { method: "POST", form }));
      return "Photo updated.";
    });
  };
  const photo = mediaUrl(user.photo_url);

  return (
    <Card title="Profile">
      <form onSubmit={save} className="space-y-4">
        {banner}
        <div className="flex items-center gap-4">
          {photo ? (
            <img src={photo} alt="" className="h-16 w-16 rounded-full object-cover" />
          ) : (
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gray-200 text-xl text-gray-500">{user.full_name[0]}</div>
          )}
          <label className="cursor-pointer text-sm text-brand-600 hover:underline">
            Change photo
            <input type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={(e) => upload(e.target.files?.[0])} />
          </label>
        </div>
        <Field id="name" label="Full name" required value={name} onChange={(e) => setName(e.target.value)} />
        <Field id="email" label="Email" value={user.email} disabled />
        <Field id="phone" label="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+61 400 000 000" />
        <Field id="dob" label="Date of birth" type="date" value={dob} onChange={(e) => setDob(e.target.value)} hint="You must be 18 or older to book." />
        <Button type="submit" loading={busy}>
          Save profile
        </Button>
      </form>
    </Card>
  );
}

function SocialCard({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [url, setUrl] = useState("");
  const { run, busy, banner } = useAction();
  return (
    <Card title="Social profiles">
      <p className="mb-3 text-sm text-gray-600">Hosts see these when you request a stay. At least one is required to send a booking request.</p>
      <div className="space-y-3">
        {banner}
        <ul className="space-y-2">
          {user.social_links.map((l) => (
            <li key={l.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-sm">
              <span>
                <strong>{l.platform}</strong> · <a href={l.url} target="_blank" rel="noreferrer noopener" className="text-brand-600 hover:underline">{l.url}</a>
              </span>
              <button className="text-xs text-red-600 hover:underline" onClick={() => run(async () => setUser(await api<User>(`/me/social-links/${l.id}`, { method: "DELETE" })))}>
                Remove
              </button>
            </li>
          ))}
          {user.social_links.length === 0 && <li className="text-sm text-gray-500">No profiles added yet.</li>}
        </ul>
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              setUser(await api<User>("/me/social-links", { method: "POST", body: { url } }));
              setUrl("");
            });
          }}
        >
          <div className="flex-1">
            <Field id="social" label="Profile link" placeholder="instagram.com/yourname" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </div>
          <Button type="submit" loading={busy}>
            Add
          </Button>
        </form>
      </div>
    </Card>
  );
}

function PasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const { run, busy, banner } = useAction();
  return (
    <Card title="Change password">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await api("/auth/change-password", { method: "POST", body: { current_password: current, new_password: next } });
            setCurrent("");
            setNext("");
            return "Password changed. Other devices were signed out.";
          });
        }}
      >
        {banner}
        <Field id="cur" label="Current password" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        <Field id="new" label="New password" type="password" autoComplete="new-password" minLength={10} required value={next} onChange={(e) => setNext(e.target.value)} hint="At least 10 characters, with a lowercase letter, an uppercase letter and a number" />
        <Button type="submit" loading={busy}>
          Change password
        </Button>
      </form>
    </Card>
  );
}

function EmailCard({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(Boolean(user.pending_email));
  const { run, busy, banner } = useAction();
  return (
    <Card title="Change email">
      <div className="space-y-4">
        {banner}
        {!sent ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api("/me/email/change", { method: "POST", body: { new_email: email, password } });
                setSent(true);
                return "We sent a code to the new address.";
              });
            }}
          >
            <Field id="newemail" label="New email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            <Field id="emailpw" label="Current password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            <Button type="submit" loading={busy}>
              Send code
            </Button>
          </form>
        ) : (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                setUser(await api<User>("/me/email/confirm", { method: "POST", body: { code } }));
                setSent(false);
                setCode("");
                return "Email updated.";
              });
            }}
          >
            <Field id="emailcode" label={`Code sent to ${user.pending_email ?? "the new address"}`} inputMode="numeric" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} />
            <Button type="submit" loading={busy}>
              Confirm
            </Button>
          </form>
        )}
      </div>
    </Card>
  );
}

function SessionsCard() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ["sessions"], queryFn: () => api<SessionInfo[]>("/auth/sessions") });
  const { run, banner } = useAction();
  return (
    <Card title="Active sessions">
      {banner}
      <ul className="space-y-2">
        {data?.map((s) => (
          <li key={s.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-sm">
            <span>
              {s.current && <strong className="mr-2 text-green-700">This device</strong>}
              <span className="text-gray-600">{(s.user_agent ?? "Unknown device").slice(0, 60)}</span>
              <span className="block text-xs text-gray-500">
                {s.ip} · {new Date(s.created_at).toLocaleString()}
              </span>
            </span>
            {!s.current && (
              <button className="text-xs text-red-600 hover:underline" onClick={() => run(async () => { await api(`/auth/sessions/${s.id}`, { method: "DELETE" }); await queryClient.invalidateQueries({ queryKey: ["sessions"] }); })}>
                Sign out
              </button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function TwoFactorCard({ user }: { user: User }) {
  const { setUser } = useAuth();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const { run, busy, banner } = useAction();
  const refresh = async () => setUser(await api<User>("/me"));

  return (
    <Card title="Two-factor authentication (admin)">
      <div className="space-y-4">
        {banner}
        {user.totp_enabled ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api("/auth/2fa/disable", { method: "POST", body: { password, code } });
                setPassword("");
                setCode("");
                await refresh();
                return "Two-factor authentication disabled.";
              });
            }}
          >
            <Alert kind="success">Two-factor authentication is on.</Alert>
            <Field id="2fapw" label="Password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            <Field id="2facode" label="Authentication code" inputMode="numeric" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} />
            <Button type="submit" variant="danger" loading={busy}>
              Disable
            </Button>
          </form>
        ) : setup ? (
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api("/auth/2fa/enable", { method: "POST", body: { code } });
                setSetup(null);
                setCode("");
                await refresh();
                return "Two-factor authentication enabled.";
              });
            }}
          >
            <p className="text-sm text-gray-600">Add this key to your authenticator app (Google Authenticator, Authy, 1Password), then enter the 6-digit code.</p>
            <code className="block break-all rounded bg-gray-100 p-2 text-sm">{setup.secret}</code>
            <a href={setup.otpauth_uri} className="text-sm text-brand-600 hover:underline">
              Open in authenticator app
            </a>
            <Field id="2fa-enable" label="Authentication code" inputMode="numeric" maxLength={6} required value={code} onChange={(e) => setCode(e.target.value)} />
            <Button type="submit" loading={busy}>
              Enable
            </Button>
          </form>
        ) : (
          <Button onClick={() => run(async () => setSetup(await api("/auth/2fa/setup", { method: "POST" })))} loading={busy}>
            Set up two-factor authentication
          </Button>
        )}
      </div>
    </Card>
  );
}

function DeactivateCard() {
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [open, setOpen] = useState(false);
  const { run, busy, banner } = useAction();
  return (
    <Card title="Deactivate account">
      {!open ? (
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Deactivate my account
        </Button>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run(async () => {
              await api("/me/deactivate", { method: "POST", body: { password } });
              setUser(null);
              navigate("/");
            });
          }}
        >
          {banner}
          <Alert kind="info">Your account will be deactivated and you will be signed out everywhere. Booking history is kept.</Alert>
          <Field id="deact" label="Confirm with your password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <div className="flex gap-2">
            <Button type="submit" variant="danger" loading={busy}>
              Deactivate
            </Button>
            <Button type="button" variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Card>
  );
}

export default function AccountPage() {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold">Your account</h1>
      <ProfileCard user={user} />
      <SocialCard user={user} />
      <PasswordCard />
      <EmailCard user={user} />
      <SessionsCard />
      {user.is_admin && <TwoFactorCard user={user} />}
      {!user.is_admin && <DeactivateCard />}
    </div>
  );
}
