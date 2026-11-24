import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, catStyle, eur, monthName, type Overview, type Report } from "../api";
import { Glass } from "../glass/Glass";
import { AreaChart, Bars, Donut } from "../ui/charts";
import { Money, Pig, rise, stagger } from "../ui/bits";
import ReactMarkdown from "react-markdown";

export function Home() {
  const [data, setData] = useState<Overview | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.overview().then(setData).catch((e) => setError(e.message));
    api.reports().then((r) => setReport(r[0] ?? null)).catch(() => undefined);
  }, []);

  if (error) return <div className="page"><Glass className="card">{error}</Glass></div>;
  if (!data) return <HomeSkeleton />;

  const month = data.month.current;
  const usual = data.month.usual_spending_cents;
  const delta = usual ? month.spending_cents - usual : null;
  const first = data.balance[0]?.cents ?? 0;
  const trend = data.net_worth.total_cents - first;
  const categories = Object.entries(month.by_category) as [string, number][];
  const movers = Object.entries(data.month.categories).filter(([, c]) => Math.abs(c.delta_cents) > 1000).slice(0, 4);

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <motion.div variants={rise} className="header">
        <div>
          <div className="muted" style={{ textTransform: "capitalize" }}>{new Date().toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" })}</div>
          <h1 className="title">Il tuo salvadanaio</h1>
        </div>
        <Pig />
      </motion.div>

      {data.open_requests > 0 && (
        <motion.div variants={rise}>
          <Glass className="banner" variant="tight">
            <span>📬 {data.open_requests === 1 ? "Un conto aspetta" : `${data.open_requests} conti aspettano`} l'estratto conto del mese scorso.</span>
            <Link to="/conti" className="btn ghost" style={{ textDecoration: "none", padding: "8px 14px" }}>Carica</Link>
          </Glass>
        </motion.div>
      )}

      <div className="grid">
        <motion.div variants={rise} className="span-7">
          <Glass refract strength={42} className="card" style={{ minHeight: 300 }}>
            <h3>Patrimonio</h3>
            <div className="big"><Money cents={data.net_worth.total_cents} /></div>
            <div className={`num ${trend >= 0 ? "up" : "down"}`} style={{ margin: "8px 0 18px", fontWeight: 600 }}>
              {trend >= 0 ? "▲" : "▼"} {eur(Math.abs(trend))} negli ultimi 6 mesi
            </div>
            <AreaChart data={data.balance.filter((_, i, a) => i % 3 === 0 || i === a.length - 1)} />
          </Glass>
        </motion.div>

        <motion.div variants={rise} className="span-5" style={{ display: "grid", gap: 18 }}>
          <Glass className="card">
            <h3>Speso a {monthName(month.month).split(" ")[0]}</h3>
            <div style={{ fontSize: 40, fontWeight: 800, letterSpacing: "-0.04em" }}><Money cents={month.spending_cents} /></div>
            {delta !== null && (
              <div className={`num ${delta <= 0 ? "up" : "down"}`} style={{ marginTop: 6, fontWeight: 600 }}>
                {delta <= 0 ? `${eur(-delta)} meno del solito` : `${eur(delta)} più del solito`}
              </div>
            )}
            <SpendBar spent={month.spending_cents} usual={usual} />
          </Glass>
          <Glass className="card">
            <h3>Entrate del mese</h3>
            <div className="row between">
              <div style={{ fontSize: 30, fontWeight: 800, letterSpacing: "-0.03em" }}><Money cents={month.income_cents} /></div>
              {month.saving_rate !== null && (
                <div className="chip on num" title="Quota delle entrate che resta">risparmi {Math.round(month.saving_rate * 100)}%</div>
              )}
            </div>
          </Glass>
        </motion.div>

        <motion.div variants={rise} className="span-6">
          <Glass className="card" style={{ height: "100%" }}>
            <h3>Dove vanno i soldi</h3>
            {categories.length ? <Donut data={categories} total={month.spending_cents} /> : <Empty text="Nessuna spesa questo mese." />}
          </Glass>
        </motion.div>

        <motion.div variants={rise} className="span-6">
          <Glass className="card" style={{ height: "100%" }}>
            <h3>Ultimi 12 mesi</h3>
            <Bars data={data.series} />
          </Glass>
        </motion.div>

        {movers.length > 0 && (
          <motion.div variants={rise} className="span-6">
            <Glass className="card">
              <h3>Rispetto al solito</h3>
              <div className="list">
                {movers.map(([name, c]) => (
                  <div key={name} className="item">
                    <div className="dot" style={{ background: `${catStyle(name).color}26` }}>{catStyle(name).emoji}</div>
                    <div style={{ flex: 1 }}><span style={{ textTransform: "capitalize" }}>{name}</span><div className="faint num" style={{ fontSize: 13 }}>di solito {eur(c.usual_cents, { compact: true })}</div></div>
                    <div className={`num ${c.delta_cents > 0 ? "down" : "up"}`} style={{ fontWeight: 600 }}>{eur(c.delta_cents, { sign: true, compact: true })}</div>
                  </div>
                ))}
              </div>
            </Glass>
          </motion.div>
        )}

        <motion.div variants={rise} className={movers.length ? "span-6" : "span-12"}>
          <Glass className="card">
            <h3>Abbonamenti e ricorrenti</h3>
            {data.recurring.length ? (
              <div className="list">
                {data.recurring.slice(0, 6).map((r) => (
                  <div key={r.name} className="item">
                    <div className="dot" style={{ background: `${catStyle(r.category).color}26` }}>{catStyle(r.category || "abbonamenti").emoji}</div>
                    <div className="ellipsis" style={{ flex: 1 }}>{r.name}{r.increased && <span className="chip" style={{ marginLeft: 8, color: "var(--warn)" }}>aumentato</span>}</div>
                    <div className="num">{eur(r.monthly_cents)}<span className="faint">/mese</span></div>
                  </div>
                ))}
                <div className="item faint" style={{ justifyContent: "space-between" }}>
                  <span>Totale all'anno</span>
                  <span className="num">{eur(data.recurring.reduce((s, r) => s + r.monthly_cents, 0) * 12, { compact: true })}</span>
                </div>
              </div>
            ) : <Empty text="Li trovo da soli dopo qualche mese di movimenti." />}
          </Glass>
        </motion.div>

        {report && (
          <motion.div variants={rise} className="span-12">
            <Glass className="card">
              <div className="row between"><h3>Il resoconto di {monthName(report.month)}</h3><Link to="/resoconti" className="faint">tutti →</Link></div>
              <div className="md" style={{ maxHeight: 260, overflow: "hidden", maskImage: "linear-gradient(#000 70%, transparent)" }}>
                <ReactMarkdown>{report.content}</ReactMarkdown>
              </div>
            </Glass>
          </motion.div>
        )}
      </div>
    </motion.div>
  );
}

function SpendBar({ spent, usual }: { spent: number; usual: number | null }) {
  if (!usual) return null;
  const pct = Math.min(1.4, spent / usual);
  const day = new Date();
  const monthPct = day.getDate() / new Date(day.getFullYear(), day.getMonth() + 1, 0).getDate();
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ position: "relative", height: 10, borderRadius: 99, background: "var(--line)", overflow: "visible" }}>
        <motion.div initial={{ width: 0 }} animate={{ width: `${(pct / 1.4) * 100}%` }} transition={{ type: "spring", stiffness: 120, damping: 20, delay: 0.3 }}
          style={{ height: "100%", borderRadius: 99, background: pct > monthPct + 0.1 ? "linear-gradient(90deg,var(--peach),var(--down))" : "linear-gradient(90deg,var(--mint),var(--pink-2))" }} />
        <div title="Il solito" style={{ position: "absolute", left: `${(1 / 1.4) * 100}%`, top: -4, bottom: -4, width: 2, background: "var(--fg-2)", borderRadius: 2 }} />
      </div>
      <div className="row between faint" style={{ fontSize: 12, marginTop: 6 }}>
        <span>siamo al {Math.round(monthPct * 100)}% del mese</span><span>il solito: {eur(usual, { compact: true })}</span>
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="faint" style={{ padding: "18px 0" }}>{text}</div>;
}

function HomeSkeleton() {
  return (
    <div className="page">
      <div className="skeleton" style={{ height: 60, width: 280 }} />
      <div className="grid">
        <div className="span-7 skeleton" style={{ height: 300, borderRadius: 28 }} />
        <div className="span-5 skeleton" style={{ height: 300, borderRadius: 28 }} />
      </div>
    </div>
  );
}
