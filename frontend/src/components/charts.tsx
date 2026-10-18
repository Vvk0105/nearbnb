// Small hand-made SVG charts (no chart library): fast, light, and they print well.

export type Point = { label: string; value: number };

/** Smooth-looking area chart. `format` turns a value into the text shown on hover (an SVG tooltip). */
export function AreaChart({ points, format = String, height = 160, ariaLabel }: { points: Point[]; format?: (v: number) => string; height?: number; ariaLabel: string }) {
  const w = 600;
  const pad = { l: 4, r: 4, t: 10, b: 20 };
  const max = Math.max(1, ...points.map((p) => p.value));
  const n = Math.max(1, points.length - 1);
  const x = (i: number) => pad.l + (i / n) * (w - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const area = points.length ? `${line} L${x(points.length - 1).toFixed(1)},${height - pad.b} L${x(0).toFixed(1)},${height - pad.b} Z` : "";
  const ticks = points.length > 1 ? [0, Math.floor(n / 2), n] : [0];
  return (
    <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={ariaLabel} className="h-auto w-full">
      <defs>
        <linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#14b8a6" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#14b8a6" stopOpacity="0.02" />
        </linearGradient>
      </defs>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={pad.l} x2={w - pad.r} y1={pad.t + f * (height - pad.t - pad.b)} y2={pad.t + f * (height - pad.t - pad.b)} stroke="#e2e8f0" strokeDasharray="3 4" />
      ))}
      <line x1={pad.l} x2={w - pad.r} y1={height - pad.b} y2={height - pad.b} stroke="#cbd5e1" />
      {area && <path d={area} fill="url(#area-fill)" />}
      {line && <path d={line} fill="none" stroke="#0d9488" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" />}
      {points.map((p, i) => (
        <g key={p.label}>
          <circle cx={x(i)} cy={y(p.value)} r={p.value > 0 ? 3 : 1.5} fill={p.value > 0 ? "#0d9488" : "#cbd5e1"} />
          <rect x={x(i) - (w / points.length) / 2} y={0} width={w / points.length} height={height} fill="transparent">
            <title>{`${p.label}: ${format(p.value)}`}</title>
          </rect>
        </g>
      ))}
      {ticks.map((i) => (
        <text key={i} x={x(i)} y={height - 4} fontSize="11" fill="#64748b" textAnchor={i === 0 ? "start" : i === n ? "end" : "middle"}>
          {points[i]?.label.slice(5)}
        </text>
      ))}
    </svg>
  );
}

/** Tiny line for the corner of a stat card. */
export function Sparkline({ values, className = "h-10 w-24" }: { values: number[]; className?: string }) {
  if (values.length < 2) return null;
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * 100).toFixed(1)},${(30 - (v / max) * 26 - 2).toFixed(1)}`).join(" ");
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className={className} aria-hidden="true">
      <polyline points={pts} fill="none" stroke="#14b8a6" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Vertical bars, one per point. */
export function BarChart({ points, format = String, height = 140, ariaLabel }: { points: Point[]; format?: (v: number) => string; height?: number; ariaLabel: string }) {
  const w = 600;
  const max = Math.max(1, ...points.map((p) => p.value));
  const slot = w / Math.max(1, points.length);
  const bar = Math.max(2, slot * 0.62);
  const base = height - 20;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} role="img" aria-label={ariaLabel} className="h-auto w-full">
      <line x1="0" x2={w} y1={base} y2={base} stroke="#cbd5e1" />
      {points.map((p, i) => {
        const h = (p.value / max) * (base - 8);
        return (
          <g key={p.label}>
            <rect x={i * slot + (slot - bar) / 2} y={base - Math.max(h, p.value > 0 ? 2 : 0)} width={bar} height={Math.max(h, p.value > 0 ? 2 : 0)} rx="2" fill={p.value > 0 ? "#14b8a6" : "#e2e8f0"} />
            <rect x={i * slot} y={0} width={slot} height={height} fill="transparent"><title>{`${p.label}: ${format(p.value)}`}</title></rect>
          </g>
        );
      })}
      {points.length > 1 && (
        <>
          <text x="0" y={height - 4} fontSize="11" fill="#64748b">{points[0].label.slice(5)}</text>
          <text x={w} y={height - 4} fontSize="11" fill="#64748b" textAnchor="end">{points[points.length - 1].label.slice(5)}</text>
        </>
      )}
    </svg>
  );
}

/** A horizontal bar for "how much of the whole". */
export function ProgressBar({ value, max, tone = "brand" }: { value: number; max: number; tone?: "brand" | "amber" | "red" | "slate" }) {
  const pct = max <= 0 ? 0 : Math.min(100, Math.round((value / max) * 100));
  const color = { brand: "bg-brand-500", amber: "bg-amber-500", red: "bg-red-500", slate: "bg-slate-400" }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}
