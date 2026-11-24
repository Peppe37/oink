/* Hand-made SVG charts: they draw themselves in, show the value under the finger, and keep to
   two or three colours so the numbers stay the point. */

import { motion } from "framer-motion";
import { useMemo, useState } from "react";
import { catStyle, eur, monthName } from "../api";

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

export function AreaChart({ data, height = 180 }: { data: { day: string; cents: number }[]; height?: number }) {
  const width = 600;
  const [hover, setHover] = useState<number | null>(null);
  const { line, area, points, min, max } = useMemo(() => {
    const values = data.map((d) => d.cents);
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 100;
    const y = (v: number) => height - 8 - ((v - (lo - pad)) / (hi + pad - (lo - pad))) * (height - 24);
    const pts: [number, number][] = data.map((d, i) => [(i / Math.max(1, data.length - 1)) * width, y(d.cents)]);
    const l = smooth(pts);
    return { line: l, area: `${l} L${width},${height} L0,${height} Z`, points: pts, min: lo, max: hi };
  }, [data, height]);
  if (data.length < 2) return <div className="faint" style={{ height }}>Pochi dati per il grafico.</div>;
  const shown = hover ?? data.length - 1;
  return (
    <div style={{ position: "relative" }}>
      <div className="row between" style={{ marginBottom: 6 }}>
        <span className="muted num">{new Date(data[shown].day).toLocaleDateString("it-IT", { day: "numeric", month: "short" })}</span>
        <span className="num" style={{ fontWeight: 700 }}>{eur(data[shown].cents)}</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" height={height} preserveAspectRatio="none" style={{ display: "block", overflow: "visible", touchAction: "pan-y" }}
        onPointerMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          setHover(Math.max(0, Math.min(data.length - 1, Math.round(((e.clientX - box.left) / box.width) * (data.length - 1)))));
        }}
        onPointerLeave={() => setHover(null)}
        role="img" aria-label={`Saldo: minimo ${eur(min)}, massimo ${eur(max)}`}>
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

export function Bars({ data }: { data: { month: string; income_cents: number; spending_cents: number }[] }) {
  const max = Math.max(1, ...data.flatMap((d) => [d.income_cents, d.spending_cents]));
  const [hover, setHover] = useState<number>(data.length - 1);
  const current = data[hover];
  return (
    <div>
      <div className="row between wrap" style={{ marginBottom: 12, gap: 8 }}>
        <span className="muted" style={{ textTransform: "capitalize" }}>{current ? monthName(current.month) : ""}</span>
        <span className="row" style={{ gap: 14 }}>
          <span className="num"><Swatch color="var(--mint)" /> {eur(current?.income_cents ?? 0, { compact: true })}</span>
          <span className="num"><Swatch color="var(--pink)" /> {eur(current?.spending_cents ?? 0, { compact: true })}</span>
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: 6, alignItems: "end", height: 140 }}>
        {data.map((d, i) => (
          <div key={d.month} onPointerEnter={() => setHover(i)} style={{ display: "flex", gap: 2, alignItems: "end", height: "100%", cursor: "default", opacity: hover === i ? 1 : 0.62, transition: "opacity .2s" }}>
            {[{ v: d.income_cents, c: "var(--mint)" }, { v: d.spending_cents, c: "var(--pink)" }].map((bar, j) => (
              <motion.div key={j} initial={{ height: 0 }} animate={{ height: `${(bar.v / max) * 100}%` }}
                transition={{ type: "spring", stiffness: 160, damping: 22, delay: i * 0.03 + j * 0.05 }}
                style={{ flex: 1, background: bar.c, borderRadius: "6px 6px 3px 3px", minHeight: bar.v ? 3 : 0 }} />
            ))}
          </div>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: 6, marginTop: 6 }}>
        {data.map((d) => (
          <span key={d.month} className="faint" style={{ fontSize: 11, textAlign: "center" }}>
            {new Date(`${d.month}-01`).toLocaleDateString("it-IT", { month: "narrow" })}
          </span>
        ))}
      </div>
    </div>
  );
}

function Swatch({ color }: { color: string }) {
  return <i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 3, background: color, marginRight: 4 }} />;
}

export function Donut({ data, total }: { data: [string, number][]; total: number }) {
  const size = 180;
  const r = 70;
  const c = 2 * Math.PI * r;
  const top = data.slice(0, 6);
  const rest = data.slice(6).reduce((s, [, v]) => s + v, 0);
  const slices: [string, number][] = rest ? [...top, ["altro", rest]] : top;
  const [active, setActive] = useState<number | null>(null);
  let offset = 0;
  const label = active !== null ? slices[active] : null;
  return (
    <div className="row wrap" style={{ gap: 20, justifyContent: "center" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Spese per categoria">
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          {slices.map(([name, value], i) => {
            const len = total ? (value / total) * c : 0;
            const gap = slices.length > 1 ? 3 : 0;
            const el = (
              <motion.circle key={name} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={catStyle(name).color}
                strokeWidth={active === i ? 26 : 20} strokeLinecap="butt"
                strokeDasharray={`${Math.max(0, len - gap)} ${c}`} strokeDashoffset={-offset}
                initial={{ strokeDasharray: `0 ${c}` }} animate={{ strokeDasharray: `${Math.max(0, len - gap)} ${c}` }}
                transition={{ duration: 0.9, delay: 0.15 + i * 0.07, ease: [0.22, 1, 0.36, 1] }}
                onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)} style={{ cursor: "pointer", transition: "stroke-width .25s" }} />
            );
            offset += len;
            return el;
          })}
        </g>
        <text x="50%" y="47%" textAnchor="middle" fill="var(--fg-2)" fontSize="12">{label ? label[0] : "spese"}</text>
        <text x="50%" y="58%" textAnchor="middle" fill="var(--fg)" fontSize="19" fontWeight="800">{eur(label ? label[1] : total, { compact: true })}</text>
      </svg>
      <div className="list" style={{ minWidth: 180, flex: 1 }}>
        {slices.map(([name, value], i) => (
          <div key={name} className="row between" style={{ padding: "6px 0", opacity: active === null || active === i ? 1 : 0.45, transition: "opacity .2s" }}
            onPointerEnter={() => setActive(i)} onPointerLeave={() => setActive(null)}>
            <span className="row" style={{ gap: 8 }}><span>{catStyle(name).emoji}</span><span style={{ textTransform: "capitalize" }}>{name}</span></span>
            <span className="num muted">{total ? Math.round((value / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
