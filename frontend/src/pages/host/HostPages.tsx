import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { label } from "../../lib/format";
import type { BookingList, HostInfo } from "../../lib/types";
import { HostDashboard } from "../../components/HostDashboard";
import { BecomeHost } from "./BecomeHost";
import { Alert, Button, Card, Field, Select, StatusBadge, TextArea, errorMessage } from "../../components/ui";

export function useHost() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["host"], queryFn: () => api<HostInfo>("/host") });
  const setHost = (host: HostInfo) => queryClient.setQueryData(["host"], host);
  return { host: query.data, loading: query.isLoading, error: query.error, setHost, refetch: query.refetch };
}

function Steps({ host }: { host: HostInfo }) {
  const steps = host.steps ?? [];
  const done = steps.filter((s) => s.done).length;
  if (steps.length === 0) return null;
  return (
    <section aria-label="Setup progress" className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between text-sm">
        <span className="font-medium">Getting set up</span>
        <span className="text-slate-500">{done} of {steps.length} done</span>
      </div>
      <div className="mb-4 h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <div className="h-full rounded-full bg-brand-500 transition-all" style={{ width: `${(done / steps.length) * 100}%` }} />
      </div>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map((s, i) => (
          <li key={s.key} className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-sm ${s.done ? "border-green-200 bg-green-50 text-green-800" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${s.done ? "bg-green-600 text-white" : "bg-slate-200 text-slate-600"}`}>{s.done ? "✓" : i + 1}</span>
            {s.label}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function HostHome() {
  const { host, loading } = useHost();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  if (loading || !host) return <p className="text-gray-500">Loading...</p>;

  if (host.state === "none") return <BecomeHost />;

  const next: Record<string, { text: string; to: string; cta: string }> = {
    draft: { text: "Finish your application and submit it for review.", to: "/host/apply", cta: "Continue application" },
    changes_requested: { text: "We need a few changes before we can approve you.", to: "/host/apply", cta: "Update application" },
    rejected: { text: "Your application was not approved. You can update it and apply again.", to: "/host/apply", cta: "Review application" },
    pending_review: { text: "We are reviewing your application. You will get an email with the result.", to: "/host/apply", cta: "View application" },
    approved: { text: "You are approved. Set up payouts to become an active host.", to: "/host/payouts", cta: "Set up payouts" },
    restricted: { text: "Stripe needs more information before you can receive payouts.", to: "/host/payouts", cta: "Fix payout setup" },
    active: { text: "You are an active host.", to: "/host/properties", cta: "Manage properties" },
    suspended: { text: "Your host account is suspended.", to: "/host/apply", cta: "View details" },
  };
  const action = next[host.state];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{host.state === "active" ? "Host dashboard" : host.state === "approved" || host.state === "restricted" ? "Finish setting up" : "Your host application"}</h1>
        <StatusBadge status={host.state} />
      </div>
      <Steps host={host} />
      {host.review_note && host.state !== "approved" && host.state !== "active" && <Alert kind="info">Message from the NearBNB team: {host.review_note}</Alert>}
      {host.suspended_reason && <Alert>Suspended: {host.suspended_reason}</Alert>}
      {host.state !== "active" && (
        <Card>
          <p className="text-sm text-gray-700">{action?.text}</p>
          {action && <div className="mt-4"><Link to={action.to}><Button>{action.cta}</Button></Link></div>}
        </Card>
      )}
      {host.state === "active" ? <HostDashboard /> : null}
      {host.can_create_listings && (
        <Card title="Your listings">
          <p className="text-sm text-gray-600">{host.can_submit_listings ? "Create properties and submit them for approval." : "You can start drafting properties now. Submitting for approval unlocks once payout setup is complete."}</p>
          <div className="mt-4"><Link to="/host/properties"><Button variant="secondary">Open properties</Button></Link></div>
        </Card>
      )}
    </div>
  );
}

const DOC_LABELS: Record<string, string> = { government_id: "Government ID (required)", selfie: "Selfie holding your ID (optional)", business_proof: "Business proof (optional)" };

export function ApplyPage() {
  const { host, loading, setHost } = useHost();
  const navigate = useNavigate();
  const [form, setForm] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const [docType, setDocType] = useState("government_id");
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (host && host.state === "none") {
      api<HostInfo>("/host/apply", { method: "POST" }).then(setHost).catch((e) => setError(errorMessage(e)));
    }
  }, [host?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (host?.application && !loaded) {
      setForm(host.application);
      setLoaded(true);
    }
  }, [host, loaded]);

  if (loading || !host || host.state === "none") return error ? <Alert>{error}</Alert> : <p className="text-gray-500">Loading...</p>;

  const editable = Boolean(host.editable);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value });

  const run = async (fn: () => Promise<HostInfo>, success?: string) => {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      setHost(await fn());
      if (success) setInfo(success);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const save = (e: FormEvent) => {
    e.preventDefault();
    void run(() => api<HostInfo>("/host/application", { method: "PATCH", body: form }), "Draft saved.");
  };

  const upload = (file: File | undefined) => {
    if (!file) return;
    void run(() => {
      const fd = new FormData();
      fd.append("doc_type", docType);
      fd.append("file", file);
      return api<HostInfo>("/host/documents", { method: "POST", form: fd });
    }, "Document uploaded.");
  };

  const submit = async () => {
    setError("");
    setBusy(true);
    try {
      await api("/host/application", { method: "PATCH", body: form });
      setHost(await api<HostInfo>("/host/application/submit", { method: "POST", body: { accept_host_terms: accepted } }));
      navigate("/host");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Host application</h1>
        <StatusBadge status={host.state} />
      </div>
      <Steps host={host} />
      {host.review_note && <Alert kind="info">Message from the NearBNB team: {host.review_note}</Alert>}
      {host.suspended_reason && <Alert>Suspended: {host.suspended_reason}</Alert>}
      {!editable && <Alert kind="info">{host.state === "pending_review" ? "Your application is being reviewed and can't be edited right now." : "This application can't be edited."}</Alert>}
      {error && <Alert>{error}</Alert>}
      {info && <Alert kind="success">{info}</Alert>}

      <Card title="About you">
        <form onSubmit={save} className="space-y-4">
          <Select id="host_type" label="I am hosting as" value={form.host_type ?? ""} onChange={set("host_type")} disabled={!editable}>
            <option value="">Choose...</option>
            <option value="individual">An individual</option>
            <option value="business">A business</option>
          </Select>
          <Field id="legal_name" label="Legal full name" value={form.legal_name ?? ""} onChange={set("legal_name")} disabled={!editable} />
          {form.host_type === "business" && (
            <>
              <Field id="business_name" label="Business name" value={form.business_name ?? ""} onChange={set("business_name")} disabled={!editable} />
              <Field id="business_number" label="Business number (ABN), optional" value={form.business_number ?? ""} onChange={set("business_number")} disabled={!editable} />
            </>
          )}
          <Field id="phone" label="Phone" value={form.phone ?? ""} onChange={set("phone")} placeholder="+61 400 000 000" disabled={!editable} />
          <Field id="address_line1" label="Street address" value={form.address_line1 ?? ""} onChange={set("address_line1")} disabled={!editable} />
          <div className="grid grid-cols-2 gap-4">
            <Field id="suburb" label="Suburb / city" value={form.suburb ?? ""} onChange={set("suburb")} disabled={!editable} />
            <Field id="state" label="State" value={form.state ?? ""} onChange={set("state")} disabled={!editable} />
            <Field id="postcode" label="Postcode" value={form.postcode ?? ""} onChange={set("postcode")} disabled={!editable} />
            <Field id="country" label="Country (2 letters)" maxLength={2} value={form.country ?? ""} onChange={set("country")} disabled={!editable} />
          </div>
          <TextArea id="about" label="Tell us about your hosting" value={form.about ?? ""} onChange={set("about")} disabled={!editable} />
          {editable && <Button type="submit" variant="secondary" loading={busy}>Save draft</Button>}
        </form>
      </Card>

      <Card title="Identity documents">
        <p className="mb-3 text-sm text-gray-600">Upload a clear photo or PDF of your government-issued ID. Only NearBNB administrators can see these files.</p>
        <ul className="mb-4 space-y-2">
          {host.documents?.map((d) => (
            <li key={d.id} className="flex items-center justify-between rounded-lg border border-gray-200 px-3 py-2 text-sm">
              <span><strong>{label(d.doc_type)}</strong> · {d.original_name} · {(d.size_bytes / 1024).toFixed(0)} KB</span>
              {editable && <button className="text-xs text-red-600 hover:underline" onClick={() => run(() => api<HostInfo>(`/host/documents/${d.id}`, { method: "DELETE" }))}>Remove</button>}
            </li>
          ))}
          {host.documents?.length === 0 && <li className="text-sm text-gray-500">No documents uploaded yet.</li>}
        </ul>
        {editable && (
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[14rem] flex-1">
              <Select id="doc_type" label="Document type" value={docType} onChange={(e) => setDocType(e.target.value)}>
                {Object.entries(DOC_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </div>
            <label className="cursor-pointer rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium hover:bg-gray-50">
              Choose file
              <input type="file" accept="image/jpeg,image/png,image/webp,application/pdf" className="hidden" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          </div>
        )}
      </Card>

      {editable && (
        <Card title="Submit">
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
            <span>I have read and accept the <Link to="/legal/host_terms" target="_blank" className="text-brand-600 underline">Host Terms</Link>.</span>
          </label>
          <div className="mt-4"><Button onClick={submit} loading={busy} disabled={!accepted}>Submit application</Button></div>
        </Card>
      )}
    </div>
  );
}

export function PayoutsPage() {
  const { host, loading, setHost, refetch } = useHost();
  const [params] = useSearchParams();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (params.get("return") && host && host.state !== "none") {
      api<HostInfo>("/host/stripe/status").then(setHost).catch((e) => setError(errorMessage(e)));
    }
  }, [params, host?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading || !host) return <p className="text-gray-500">Loading...</p>;
  if (host.state === "none") return <Alert kind="info">Apply to become a host first.</Alert>;

  const approved = host.review_status === "approved";
  const stripe = host.stripe;

  const connect = async () => {
    setError("");
    setBusy(true);
    try {
      const { url } = await api<{ url: string }>("/host/stripe/onboarding-link", { method: "POST" });
      window.location.assign(url);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const check = async () => {
    setError("");
    try {
      setHost(await api<HostInfo>("/host/stripe/status"));
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  const simulate = async () => {
    setError("");
    try {
      setHost(await api<HostInfo>("/host/stripe/dev-complete", { method: "POST" }));
      void refetch();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-bold">Payout setup</h1>
        <StatusBadge status={host.state} />
      </div>
      <Steps host={host} />
      {error && <Alert>{error}</Alert>}
      {!approved && <Alert kind="info">Payout setup opens once your application is approved.</Alert>}
      {approved && stripe && (
        <Card title="Stripe account">
          <p className="mb-4 text-sm text-gray-600">NearBNB pays you through Stripe. Stripe collects your bank details and verifies your identity. We never see or store your bank details.</p>
          <ul className="mb-4 space-y-1 text-sm">
            <li>{stripe.connected ? "✓" : "○"} Account created</li>
            <li>{stripe.details_submitted ? "✓" : "○"} Details submitted</li>
            <li>{stripe.payouts_enabled ? "✓" : "○"} Payouts enabled</li>
            <li>{stripe.transfers_active ? "✓" : "○"} Transfers active</li>
          </ul>
          {stripe.requirements.length > 0 && <Alert kind="info">Stripe still needs: {stripe.requirements.map(label).join(", ")}.</Alert>}
          {stripe.ready ? (
            <Alert kind="success">Payout setup is complete.</Alert>
          ) : (
            <div className="mt-4 flex flex-wrap gap-3">
              <Button onClick={connect} loading={busy}>{stripe.connected ? "Continue on Stripe" : "Connect with Stripe"}</Button>
              {stripe.connected && <Button variant="secondary" onClick={check}>I'm done: check status</Button>}
            </div>
          )}
          {stripe.dev_mode && !stripe.ready && (
            <div className="mt-6 rounded-lg border border-dashed border-gray-300 p-3">
              <p className="mb-2 text-xs text-gray-500">Development mode: no Stripe keys are configured, so a fake Stripe is used. This button pretends you finished onboarding.</p>
              <Button variant="secondary" onClick={simulate}>Simulate completed Stripe setup</Button>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
