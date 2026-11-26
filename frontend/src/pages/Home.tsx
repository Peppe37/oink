import { motion } from "framer-motion";
import { useEffect, useState, type FormEvent } from "react";
import { Markdown as ReactMarkdown } from "../ui/Markdown";
import { Link } from "react-router-dom";
import { api, type Account, type Overview, type Report, type User } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { AreaChart, Bars, Donut } from "../ui/charts";
import { Empty, Field, Money, PageHeader, Pig, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { CategoryTile, Icon } from "../ui/icons";
import { categoryLabel } from "../ui/labels";

export function Home({ user, onAdd }: { user: User; onAdd: () => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [data, setData] = useState<Overview | null>(null);
  const [moved, setMoved] = useState<{ institution: string; currency: string; count: number; total: string }[]>([]);
  useEffect(() => { api.untrackedTransfers().then(setMoved).catch(() => undefined); }, []);
  const [missing, setMissing] = useState<{ id: number; name: string; amount: string; currency: string; due: string }[]>([]);
  useEffect(() => { api.missingFixed().then(setMissing).catch(() => undefined); }, []);
  const [paying, setPaying] = useState<{ id: number; name: string; amount: string; currency: string; due: string } | null>(null);
  const drop = (id: number) => setMissing((x) => x.filter((y) => y.id !== id));
  const [plansFound, setPlansFound] = useState(0);
  useEffect(() => { api.planProposals().then((p) => setPlansFound(p.length)).catch(() => undefined); }, []);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.overview().then(setData).catch((e) => setError(errorText(e)));
    api.reports().then((r) => setReport(r[0] ?? null)).catch(() => undefined);
  }, [errorText]);

  const hour = new Date().getHours();
  const greeting = t(hour < 12 ? "home.morning" : hour < 18 ? "home.afternoon" : "home.evening");
  const subtitle = fmt.date(new Date().toISOString(), { weekday: "long", day: "numeric", month: "long" });

  if (error) return <div className="page"><Glass className="card error">{error}</Glass></div>;
  if (!data) return <HomeSkeleton />;

  const cur = data.currency;
  const month = data.month.current;
  const usual = data.month.usual_spending;
  const delta = usual !== null ? month.spending - usual : null;
  const first = data.balance[0]?.value ?? 0;
  const trend = data.net_worth.total - first;
  const categories = Object.entries(month.by_category) as [string, number][];
  const movers = Object.entries(data.month.categories).filter(([, c]) => Math.abs(c.delta) >= 10).slice(0, 4);
  const empty = data.net_worth.accounts.length === 0;

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={`${greeting}, ${user.name || user.username}`} subtitle={<span className="cap">{subtitle}</span>} action={<Pig />} />

      {empty && (
        <motion.div variants={rise}>
          <Glass refract className="card">
            <Empty icon="savings" title={t("home.emptyTitle")} hint={t("home.emptyHint")}
              action={<button className="btn" onClick={onAdd}><Icon name="plus" size={18} /> {t("home.emptyCta")}</button>} />
          </Glass>
        </motion.div>
      )}

      {data.open_requests > 0 && (
        <motion.div variants={rise}>
          <Glass className="banner" variant="tight">
            <span className="row" style={{ gap: 10 }}><Icon name="inbox" /> {t("home.requests", { count: data.open_requests })}</span>
            <Link to="/accounts" className="btn ghost small">{t("home.upload")}</Link>
          </Glass>
        </motion.div>
      )}

      {missing.map((m) => (
        <motion.div variants={rise} key={`missing-${m.id}`}>
          <Glass className="banner" variant="tight">
            <span className="row" style={{ gap: 10 }}><Icon name="bills" /> {t("plan.missing", { name: m.name, amount: fmt.money(m.amount, m.currency), date: fmt.date(m.due, { day: "numeric", month: "long" }) })}</span>
            <span className="row" style={{ gap: 6 }}>
              <button className="btn ghost small" title={t("plan.waitHint")} onClick={async () => { await api.waitFixed(m.id); drop(m.id); }}>{t("plan.wait")}</button>
              <button className="btn ghost small" title={t("plan.paidHint")} onClick={() => setPaying(m)}>{t("plan.paid")}</button>
              <button className="btn ghost small" title={t("plan.skipHint")} onClick={async () => { await api.skipFixed(m.id); drop(m.id); }}>{t("plan.skip")}</button>
              <button className="btn ghost small" title={t("plan.stopHint")} onClick={async () => { await api.stopFixed(m.id); drop(m.id); }}>{t("plan.stop")}</button>
            </span>
          </Glass>
        </motion.div>
      ))}

      {plansFound > 0 && (
        <motion.div variants={rise}>
          <Glass className="banner" variant="tight">
            <span className="row" style={{ gap: 10 }}><Icon name="investments" /> {t("invest.foundHome", { count: plansFound })}</span>
            <Link to="/invest" className="btn ghost small">{t("invest.review")}</Link>
          </Glass>
        </motion.div>
      )}

      {moved.map((m) => (
        <motion.div variants={rise} key={m.institution + m.currency}>
          <Glass className="banner" variant="tight">
            <span className="row" style={{ gap: 10 }}><Icon name="transfers" />
              {m.institution === "?" ? t("transfers.toUnknown", { amount: fmt.money(m.total, m.currency) })
                : t("transfers.toInstitution", { amount: fmt.money(m.total, m.currency), institution: m.institution })}</span>
            <button className="btn ghost small" onClick={onAdd}>{t("transfers.add")}</button>
          </Glass>
        </motion.div>
      ))}

      {!empty && (
        <div className="grid">
          <motion.div variants={rise} className="span-7">
            <Glass refract strength={42} className="card hero">
              <h3>{t("home.netWorth")}</h3>
              <div className="big"><Money value={data.net_worth.total} currency={cur} /></div>
              <div className={`num trend ${trend >= 0 ? "up" : "down"}`}>
                <Icon name={trend >= 0 ? "arrowUp" : "arrowDown"} size={16} stroke={2.2} />
                {t("home.trend", { amount: fmt.money(Math.abs(trend), cur) })}
              </div>
              {data.net_worth.missing_rates.length > 0 && <p className="faint small-text">{t("home.missingRates", { codes: data.net_worth.missing_rates.join(", ") })}</p>}
              <AreaChart data={data.balance.filter((_, i, a) => i % 3 === 0 || i === a.length - 1)} currency={cur} />
            </Glass>
          </motion.div>

          <motion.div variants={rise} className="span-5">
            <Glass className="card month">
              <h3>{t("home.thisMonth")}</h3>
              <div className="stat">
                <span className="muted">{t("home.spent")}</span>
                <span className="mid"><Money value={month.spending} currency={cur} /></span>
              </div>
              <div className={`pill-note ${delta === null ? "" : delta > 0 ? "down" : "up"}`}>
                {delta === null || Math.abs(delta) < 1 ? t("home.asUsual")
                  : delta > 0 ? t("home.aboveUsual", { amount: fmt.money(delta, cur, { compact: true }) })
                    : t("home.belowUsual", { amount: fmt.money(-delta, cur, { compact: true }) })}
              </div>
              <div className="two stats">
                <div className="stat"><span className="muted">{t("home.earned")}</span><span className="num up strong"><Money value={month.income} currency={cur} compact /></span></div>
                <div className="stat"><span className="muted">{t("home.saved")}</span><span className={`num strong ${month.net >= 0 ? "up" : "down"}`}><Money value={month.net} currency={cur} compact /></span></div>
              </div>
              {movers.length > 0 && (
                <div className="movers">
                  <span className="faint small-text">{t("home.movers")}</span>
                  {movers.map(([cat, c]) => (
                    <div key={cat || "none"} className="row between">
                      <span className="row" style={{ gap: 8, minWidth: 0 }}><CategoryTile category={cat} size={24} /><span className="ellipsis">{categoryLabel(t, cat)}</span></span>
                      <span className={`num ${c.delta > 0 ? "down" : "up"}`}>{fmt.money(c.delta, cur, { sign: true, compact: true })}</span>
                    </div>
                  ))}
                </div>
              )}
            </Glass>
          </motion.div>

          <motion.div variants={rise} className="span-6">
            <Glass className="card">
              <h3>{t("home.whereMoney")}</h3>
              {categories.length ? <Donut data={categories} total={month.spending} currency={cur} /> : <p className="faint">{t("movements.empty")}</p>}
            </Glass>
          </motion.div>

          <motion.div variants={rise} className="span-6">
            <Glass className="card">
              <h3>{t("home.incomeSpending")}</h3>
              <Bars data={data.series} currency={cur} />
            </Glass>
          </motion.div>

          {data.recurring.length > 0 && (
            <motion.div variants={rise} className="span-6">
              <Glass className="card">
                <h3>{t("home.subscriptions")}</h3>
                <div className="list">
                  {data.recurring.slice(0, 7).map((r) => (
                    <div key={r.name} className="item">
                      <CategoryTile category={r.category} size={36} />
                      <span className="item-main">
                        <span className="row-text">
                          <span className="row-title ellipsis">{r.name}</span>
                          {r.increased && <span className="row-detail down">{t("home.increased")}</span>}
                        </span>
                        <span className="num">{t("home.perMonth", { amount: fmt.money(r.monthly, cur) })}</span>
                      </span>
                    </div>
                  ))}
                </div>
              </Glass>
            </motion.div>
          )}

          {report && (
            <motion.div variants={rise} className={data.recurring.length ? "span-6" : "span-12"}>
              <Glass className="card">
                <div className="row between"><h3>{t("home.latestReport")}</h3><span className="faint cap small-text">{fmt.month(report.month)}</span></div>
                <div className="md clamp"><ReactMarkdown>{report.content}</ReactMarkdown></div>
                <Link to="/insights" className="link">{t("nav.reports")} <Icon name="chevron" size={14} /></Link>
              </Glass>
            </motion.div>
          )}
        </div>
      )}
    <Sheet open={!!paying} onClose={() => setPaying(null)} title={paying ? t("plan.paidTitle", { name: paying.name }) : ""}>
        {paying && <PaidElsewhere cost={paying} onDone={() => { drop(paying.id); setPaying(null); }} />}
      </Sheet>
    </motion.div>
  );
}

function HomeSkeleton() {
  return (
    <div className="page">
      <div className="skeleton" style={{ height: 56, width: "60%" }} />
      <div className="grid">
        <div className="span-7 skeleton" style={{ height: 320 }} />
        <div className="span-5 skeleton" style={{ height: 320 }} />
      </div>
    </div>
  );
}

/* «Paid another way» (cash…): the payment goes on the account it came from, so it counts
   as spent; when that account's balance already reflects it, the balance stays as it is. */
function PaidElsewhere({ cost, onDone }: { cost: { id: number; name: string; amount: string; currency: string; due: string }; onDone: () => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [day, setDay] = useState(cost.due);
  const [counted, setCounted] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.accounts().then((list) => {
      const sorted = [...list].sort((a, b) => Number(b.kind === "cash") - Number(a.kind === "cash"));
      setAccounts(sorted);
      setAccountId(sorted[0]?.id ?? null);
    }).catch((e) => setError(errorText(e)));
  }, [errorText]);
  const account = accounts?.find((a) => a.id === accountId);
  const manual = account?.source !== "linked";
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (accountId == null) return;
    setBusy(true);
    try { await api.paidFixed(cost.id, { account_id: accountId, day, balance_updated: manual && counted }); onDone(); }
    catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  if (!accounts) return <Spinner />;
  return (
    <form className="stack" onSubmit={submit}>
      <p className="muted">{t("plan.paidIntro", { amount: fmt.money(cost.amount, cost.currency) })}</p>
      <Field label={t("plan.paidFrom")}>
        <select className="field" value={accountId ?? ""} onChange={(e) => setAccountId(Number(e.target.value))} required>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution && a.institution !== a.name ? ` · ${a.institution}` : ""}</option>)}
        </select>
      </Field>
      <Field label={t("plan.paidOn")}><input className="field" type="date" value={day} onChange={(e) => setDay(e.target.value)} required /></Field>
      {manual && (
        <label className="check"><input type="checkbox" checked={counted} onChange={(e) => setCounted(e.target.checked)} /> {t("plan.paidCounted")}</label>
      )}
      <p className="faint small-text">{manual && counted ? t("plan.paidCountedHint") : t("plan.paidBalanceHint")}</p>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" disabled={busy || accountId == null}>{busy ? <Spinner /> : t("plan.paidConfirm")}</button>
    </form>
  );
}
