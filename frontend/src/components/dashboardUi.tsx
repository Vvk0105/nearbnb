import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "./icons";
import { Sparkline } from "./charts";

/** A white rounded block with a title, used for every section of a dashboard. */
export function Panel({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`}>
      <div className="mb-4 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

/** A headline number with an icon and an optional trend line. Label first, value second, so screen readers read it naturally. */
export function Kpi({ label, value, hint, icon, spark, to }: { label: string; value: string | number; hint?: string; icon: IconName; spark?: number[]; to?: string }) {
  const body = (
    <>
      <div className="flex items-start justify-between">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-700"><Icon name={icon} /></span>
        {spark && <Sparkline values={spark} />}
        {to && !spark && <Icon name="arrow" className="h-4 w-4 text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-brand-600" />}
      </div>
      <dl className="mt-3">
        <dt className="text-sm text-slate-500">{label}</dt>
        <dd className="mt-0.5 text-2xl font-semibold tracking-tight">{value}</dd>
      </dl>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </>
  );
  const cls = "block rounded-2xl border border-slate-200 bg-white p-5 shadow-sm";
  return to ? <Link to={to} className={`group ${cls} transition hover:border-brand-300 hover:shadow`}>{body}</Link> : <div className={cls}>{body}</div>;
}

export function EmptyState({ icon, title, children }: { icon: IconName; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-100 text-slate-400"><Icon name={icon} /></span>
      <p className="mt-3 text-sm font-medium text-slate-700">{title}</p>
      {children && <p className="mt-1 text-sm text-slate-500">{children}</p>}
    </div>
  );
}
