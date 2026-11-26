/* Hand-made SVG charts: they draw themselves in, show the value under the finger, and keep to
   two or three colours so the numbers stay the point. Values are in the main currency. */

import { motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import { categoryColor, CategoryTile } from "./icons";

function smooth(points: [number, number][]): string {
  if (points.length < 2) return "";
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    const [x0, y0] = points[i - 1];
    const [x1, y1] = points[i];
    const cx = (x0 + x1) / 2;
    d += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}`;
  }
  return d;
}

export function AreaChart({ data, currency, height = 170, label }: { data: { day: string; value: number }[]; currency: string; height?: number; label?: string }) {
  const { t, fmt } = useI18n();
  const width = 600;
  const [hover, setHover] = useState<number | null>(null);
  const { line, area, points } = useMemo(() => {
    const values = data.map((d) => d.value);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1;
    const y = (v: number) => height - 8 - ((v - (lo - pad)) / (hi + pad - (lo - pad))) * (height - 24);
    const pts: [number, number][] = data.map((d, i) => [(i / Math.max(1, data.length - 1)) * width, y(d.value)]);
    const l = smooth(pts);
    return { line: l, area: `${l} L${width},${height} L0,${height} Z`, points: pts };
  }, [data, height]);
  if (data.length < 2) return <div className="faint" style={{ height }}>{t("home.notEnoughData")}</div>;
  const shown = hover ?? data.length - 1;
  return (
    <div style={{ position: "relative" }}>
      <div className="row between" style={{ marginBottom: 6 }}>
        <span className="muted num">{fmt.date(data[shown].day)}</span>
        <span className="num strong">{fmt.money(data[shown].value, currency)}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" className="chart"
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover(Math.max(0, Math.min(data.length - 1, Math.round(((e.clientX - box.left) / box.width) * (data.length - 1)))));
        }}
        onPointerLeave={() => setHover(null)} role="img" aria-label={label ?? t("home.netWorth")}>
        <defs>
          <linearGradient id="area-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--pink)" stopOpacity="0.45" />
            <stop offset="1" stopColor="var(--pink)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <motion.path d={area} fill="url(#area-fill)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1, delay: 0.4 }} />
        <motion.path d={line} fill="none" stroke="var(--pink-2)" strokeWidth="2.5" strokeLinecap="round" vectorEffect="non-scaling-stroke"
          initial={{ pathLength: 0 } as never} animate={{ pathLength: 1 } as never} transition={{ duration: 1.4, ease: [0.22, 1, 0.36, 1] }} />
        <line x1={points[shown][0]} x2={points[shown][0]} y1={0} y2={height} stroke="var(--line)" vectorEffect="non-scaling-stroke" />
        <circle cx={points[shown][0]} cy={points[shown][1]} r="5" fill="var(--pink-2)" stroke="var(--bg)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

export function Bars({ data, currency }: { data: { month: string; income: number; spending: number }[]; currency: string }) {
  const { fmt } = useI18n();
  const max = Math.max(1, ...data.flatMap((d) => [d.income, d.spending]));
  const [hover, setHover] = useState<number>(data.length - 1);
  useEffect(() => { setHover(data.length - 1); }, [data.length]);
  const current = data[hover];
  return (
    <div>
      <div className="row between wrap" style={{ marginBottom: 12, gap: 8 }}>
        <span className="muted cap">{current ? fmt.month(current.month) : ""}</span>
        <span className="row" style={{ gap: 14 }}>
          <span className="num"><Swatch color="var(--mint)" /> {fmt.money(current?.income ?? 0, currency, { compact: true })}</span>
          <span className="num"><Swatch color="var(--pink)" /> {fmt.money(current?.spending ?? 0, currency, { compact: true })}</span>
        </span>
      </div>
      <div className="bars" style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)` }}>
        {data.map((d, i) => (
          <div key={d.month} onPointerEnter={() => setHover(i)} onClick={() => setHover(i)} className="bar-pair" style={{ opacity: hover === i ? 1 : 0.6 }}>
            {[{ v: d.income, c: "var(--mint)" }, { v: d.spending, c: "var(--pink)" }].map((bar, j) => (
              <motion.div key={j} initial={{ height: 0 }} animate={{ height: `${(bar.v / max) * 100}%` }}
                transition={{ type: "spring", stiffness: 160, damping: 22, delay: i * 0.03 + j * 0.05 }}
                style={{ flex: 1, background: bar.c, borderRadius: "6px 6px 3px 3px", minHeight: bar.v ? 3 : 0 }} />
            ))}
          </div>
        ))}
      </div>
      <div className="bars-labels" style={{ gridTemplateColumns: `repeat(${data.length}, 1fr)` }}>
        {data.map((d) => <span key={d.month}>{fmt.month(d.month, "narrow")}</span>)}
      </div>
    </div>
  );
}

function Swatch({ color }: { color: string }) {
  return <i className="swatch" style={{ background: color }} />;
}

export function Donut({ data, total, currency }: { data: [string, number][]; total: number; currency: string }) {
  const { t, fmt } = useI18n();
  const name = (c: string) => (c ? (t.has(`category.${c}`) ? t(`category.${c}` as never) : c) : t("category.none"));
  const size = 180;
  const r = 70;
  const c = 2 * Math.PI * r;
  const top = data.slice(0, 6);
  const rest = data.slice(6).reduce((s, [, v]) => s + v, 0);
  const hasOther = top.some(([k]) => k === "other");
  const slices: [string, number][] = !rest ? top
    : hasOther ? top.map(([k, v]) => [k, k === "other" ? v + rest : v] as [string, number])
    : [...top, ["other", rest]];
  const [active, setActive] = useState<number | null>(null);
  let offset = 0;
  const label = active !== null ? slices[active] : null;
  return (
    <div className="donut">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={t("home.whereMoney")}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          {slices.map(([cat, value], i) => {
            const len = total ? (value / total) * c : 0;
            const gap = slices.length > 1 ? 3 : 0;
            const el = (
              <motion.circle key={cat || "none"} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={categoryColor(cat || "none")}
                strokeWidth={active === i ? 26 : 20} strokeDasharray={`${Math.max(0, len - gap)} ${c}`} strokeDashoffset={-offset}
                initial={{ strokeDasharray: `0 ${c}` }} animate={{ strokeDasharray: `${Math.max(0, len - gap)} ${c}` }}
                transition={{ duration: 0.9, delay: 0.15 + i * 0.07, ease: [0.22, 1, 0.36, 1] }}
                onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)} style={{ cursor: "pointer", transition: "stroke-width .25s" }} />
            );
            offset += len;
            return el;
          })}
        </g>
        <text x="50%" y="47%" textAnchor="middle" fill="var(--fg-2)" fontSize="12">{label ? name(label[0]) : t("home.spending")}</text>
        <text x="50%" y="58%" textAnchor="middle" fill="var(--fg)" fontSize="19" fontWeight="800">{fmt.money(label ? label[1] : total, currency, { compact: true })}</text>
      </svg>
      <div className="legend">
        {slices.map(([cat, value], i) => (
          <div key={cat || "none"} className="row between" style={{ opacity: active === null || active === i ? 1 : 0.45 }}
            onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)}>
            <span className="row" style={{ gap: 10, minWidth: 0 }}><CategoryTile category={cat} size={26} /><span className="ellipsis">{name(cat)}</span></span>
            <span className="num muted">{total ? fmt.percent(value / total) : "0%"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Monte Carlo fan: 5–95 and 25–75 percentile bands, the median, and what was put in. */
export function FanChart({ bands, invested, currency, height = 220 }: { bands: { year: number; p5: number; p25: number; p50: number; p75: number; p95: number }[]; invested: (year: number) => number; currency: string; height?: number }) {
  const { t, fmt } = useI18n();
  const width = 600;
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(...bands.map((b) => b.p95), invested(bands.length - 1)) * 1.05 || 1;
  const x = (i: number) => (i / Math.max(1, bands.length - 1)) * width;
  const y = (v: number) => height - 6 - (v / max) * (height - 16);
  const area = (lo: keyof (typeof bands)[number], hi: keyof (typeof bands)[number]) =>
    `M${bands.map((b, i) => `${x(i)},${y(b[hi] as number)}`).join(" L")} L${[...bands].reverse().map((b, i) => `${x(bands.length - 1 - i)},${y(b[lo] as number)}`).join(" L")} Z`;
  const shown = hover ?? bands.length - 1;
  const b = bands[shown];
  return (
    <div>
      <div className="row between wrap small-text" style={{ marginBottom: 6, gap: 8 }}>
        <span className="muted">{t("invest.year", { n: b.year })}</span>
        <span className="row" style={{ gap: 12 }}>
          <span className="num"><i className="swatch" style={{ background: "var(--pink-2)" }} />{fmt.money(b.p50, currency, { compact: true })}</span>
          <span className="num faint">{fmt.money(b.p5, currency, { compact: true })} – {fmt.money(b.p95, currency, { compact: true })}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" className="chart"
        onPointerMove={(e) => { const box = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(bands.length - 1, Math.round(((e.clientX - box.left) / box.width) * (bands.length - 1))))); }}
        onPointerLeave={() => setHover(null)} role="img" aria-label={t("invest.future")}>
        <motion.path d={area("p5", "p95")} fill="var(--violet)" opacity={0.22} initial={{ opacity: 0 }} animate={{ opacity: 0.22 }} transition={{ duration: 0.8 }} />
        <motion.path d={area("p25", "p75")} fill="var(--pink)" opacity={0.3} initial={{ opacity: 0 }} animate={{ opacity: 0.3 }} transition={{ duration: 0.8, delay: 0.2 }} />
        <motion.path d={`M${bands.map((bb, i) => `${x(i)},${y(bb.p50)}`).join(" L")}`} fill="none" stroke="var(--pink-2)" strokeWidth="2.5" vectorEffect="non-scaling-stroke"
          initial={{ pathLength: 0 } as never} animate={{ pathLength: 1 } as never} transition={{ duration: 1.2 }} />
        <path d={`M${bands.map((_, i) => `${x(i)},${y(invested(i))}`).join(" L")}`} fill="none" stroke="var(--fg-3)" strokeDasharray="5 5" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        <line x1={x(shown)} x2={x(shown)} y1={0} y2={height} stroke="var(--line)" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

/** Two lines over time: what was invested and what it was worth. */
export function GrowthChart({ points, currency, height = 180 }: { points: { day: string; invested: number; value: number }[]; currency: string; height?: number }) {
  const { fmt, t } = useI18n();
  const width = 600;
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const max = Math.max(...points.map((p) => Math.max(p.value, p.invested))) * 1.05 || 1;
  const x = (i: number) => (i / (points.length - 1)) * width;
  const y = (v: number) => height - 6 - (v / max) * (height - 16);
  const line = (k: "value" | "invested") => `M${points.map((p, i) => `${x(i)},${y(p[k])}`).join(" L")}`;
  const shown = hover ?? points.length - 1;
  const p = points[shown];
  return (
    <div>
      <div className="row between wrap small-text" style={{ marginBottom: 6, gap: 8 }}>
        <span className="muted">{fmt.date(p.day, { month: "short", year: "numeric" })}</span>
        <span className="row" style={{ gap: 12 }}>
          <span className="num"><i className="swatch" style={{ background: "var(--mint)" }} />{fmt.money(p.value, currency, { compact: true })}</span>
          <span className="num faint">{t("invest.invested")} {fmt.money(p.invested, currency, { compact: true })}</span>
        </span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" className="chart"
        onPointerMove={(e) => { const box = e.currentTarget.getBoundingClientRect(); setHover(Math.max(0, Math.min(points.length - 1, Math.round(((e.clientX - box.left) / box.width) * (points.length - 1))))); }}
        onPointerLeave={() => setHover(null)} role="img" aria-label={t("invest.past")}>
        <defs><linearGradient id="growth" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="var(--mint)" stopOpacity="0.35" /><stop offset="1" stopColor="var(--mint)" stopOpacity="0" /></linearGradient></defs>
        <path d={`${line("value")} L${width},${height} L0,${height} Z`} fill="url(#growth)" />
        <motion.path d={line("value")} fill="none" stroke="var(--mint)" strokeWidth="2.5" vectorEffect="non-scaling-stroke" initial={{ pathLength: 0 } as never} animate={{ pathLength: 1 } as never} transition={{ duration: 1.2 }} />
        <path d={line("invested")} fill="none" stroke="var(--fg-3)" strokeDasharray="5 5" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
        <line x1={x(shown)} x2={x(shown)} y1={0} y2={height} stroke="var(--line)" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}
