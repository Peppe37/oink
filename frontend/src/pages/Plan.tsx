/* The planning cards in Insights: the whole picture, AI advice, fixed costs, risk profile. */

import { motion } from "framer-motion";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Markdown as ReactMarkdown } from "../ui/Markdown";
import { api, type FixedCosts, type FixedItem, type Picture, type Risk } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { Field, Row, Sheet, Spinner, rise } from "../ui/bits";
import { CategoryTile, Icon } from "../ui/icons";
import { CATEGORIES, categoryLabel } from "../ui/labels";

export function PictureCard({ picture }: { picture: Picture | null }) {
  const { t, fmt } = useI18n();
  if (!picture) return <div className="skeleton" style={{ height: 200 }} />;
  const c = picture.currency;
  const life = picture.lifestyle;
  const em = picture.emergency_months;
  return (
    <Glass refract className="card">
      <h3>{t("plan.picture")}</h3>
      <div className="tiles">
        <div className="tile-stat"><span>{t("home.netWorth")}</span><strong className="num">{fmt.money(picture.net_worth, c, { compact: true })}</strong></div>
        <div className="tile-stat"><span>{t("plan.liquid")}</span><strong className="num">{fmt.money(picture.liquid, c, { compact: true })}</strong></div>
        <div className="tile-stat"><span>{t("invest.title")}</span><strong className="num">{fmt.money(picture.investments, c, { compact: true })}</strong></div>
        <div className="tile-stat"><span>{t("plan.emergency")}</span><strong className={`num ${em !== null && em < 3 ? "down" : "up"}`}>{em === null ? "—" : t("plan.months", { n: em })}</strong></div>
        {life.saving_rate !== undefined && life.saving_rate !== null && <div className="tile-stat"><span>{t("plan.savingRate")}</span><strong className={`num ${life.saving_rate < 0.1 ? "down" : "up"}`}>{fmt.percent(life.saving_rate)}</strong></div>}
        <div className="tile-stat"><span>{t("plan.fixed")}</span><strong className="num">{fmt.money(picture.fixed_costs_monthly, c, { compact: true })}</strong></div>
        <div className="tile-stat"><span>{t("plan.pac")}</span><strong className="num">{fmt.money(picture.plans_monthly, c, { compact: true })}</strong></div>
        {picture.risk.profile && <div className="tile-stat"><span>{t("plan.profile")}</span><strong>{t(`plan.profile.${picture.risk.profile}` as never)}</strong></div>}
      </div>
    </Glass>
  );
}

export function AdviceCard({ ai, notify }: { ai: boolean; notify: (s: string) => void }) {
  const { t, lang } = useI18n();
  const errorText = useErrorText();
  const [content, setContent] = useState("");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.reports().then((r) => setContent(r.find((x) => x.kind === "advice")?.content ?? "")).catch(() => undefined); }, []);
  const ask = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { setContent((await api.advice(focus, lang)).content); } catch (err) { notify(errorText(err)); } finally { setBusy(false); }
  };
  if (!ai) return null;
  return (
    <Glass refract strength={44} className="card advice">
      <h3>{t("plan.advice")}</h3>
      <form className="ask" onSubmit={ask}>
        <input className="field" value={focus} onChange={(e) => setFocus(e.target.value)} placeholder={t("plan.focusPlaceholder")} maxLength={500} />
        <button className="btn" disabled={busy}>{busy ? <Spinner /> : <><Icon name="sparkle" size={16} /> {t("plan.adviseMe")}</>}</button>
      </form>
      {busy && <p className="muted">{t("plan.thinking")}</p>}
      {content && !busy && <motion.div className="md" initial={{ opacity: 0 }} animate={{ opacity: 1 }}><ReactMarkdown>{content}</ReactMarkdown></motion.div>}
      <p className="faint small-text">{t("plan.disclaimer")}</p>
    </Glass>
  );
}

export function FixedCard({ notify }: { notify: (s: string) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [data, setData] = useState<FixedCosts | null>(null);
  const [adding, setAdding] = useState<{ name: string; amount: string; category: string } | null>(null);
  const [editing, setEditing] = useState<FixedItem | null>(null);
  const [all, setAll] = useState(false);
  const FIRST = 5;
  const load = useCallback(() => { api.fixed().then(setData).catch((e) => notify(errorText(e))); }, [notify, errorText]);
  useEffect(load, [load]);
  if (!data) return <div className="skeleton" style={{ height: 200 }} />;
  return (
    <Glass className="card">
      <div className="row between"><h3>{t("plan.fixedTitle")}</h3>
        <button className="btn ghost small" onClick={() => setAdding({ name: "", amount: "", category: "" })}><Icon name="plus" size={16} /> {t("common.add")}</button></div>
      <div className="stat-row"><div><span className="muted small-text">{t("plan.perMonth")}</span><div className="mid num">{fmt.money(data.monthly, data.currency)}</div></div>
        <div><span className="muted small-text">{t("plan.perYear")}</span><div className="mid num faint">{fmt.money(data.yearly, data.currency, { compact: true })}</div></div></div>
      <div className="list">
        {(all ? data.items : data.items.slice(0, FIRST)).map((f) => (
          <Row key={f.id} icon={<CategoryTile category={f.category || "bills"} size={36} />} title={f.name}
            detail={`${fmt.money(f.amount, f.currency)} · ${t(`invest.freq.${f.frequency}` as never)}${f.next_due ? ` · ${fmt.date(f.next_due)}` : ""}`}
            right={<span className="row" style={{ gap: 2 }}><span className="num">{fmt.money(f.per_month, data.currency)}</span>
              <button className="icon-btn" aria-label={t("common.edit")} onClick={() => setEditing(f)}><Icon name="pencil" size={16} /></button>
              <button className="icon-btn" aria-label={t("common.delete")} onClick={async () => { await api.deleteFixed(f.id); load(); }}><Icon name="trash" size={16} /></button></span>} />
        ))}
      </div>
      {data.items.length > FIRST && (
        <button className="btn ghost small expand-btn" onClick={() => setAll((v) => !v)}>
          {all ? t("plan.showLess") : t("plan.showAll", { count: data.items.length })} <Icon name="chevron" size={14} style={{ transform: all ? "rotate(-90deg)" : "rotate(90deg)" }} />
        </button>
      )}
      {data.suggestions.length > 0 && (
        <>
          <h4 className="section-label">{t("plan.suggested")}</h4>
          <div className="list">
            {data.suggestions.map((s) => (
              <Row key={s.name} icon={<CategoryTile category={s.category} size={32} />} title={s.name} detail={t("home.perMonth", { amount: fmt.money(s.monthly, data.currency) })}
                right={<button className="btn ghost small" onClick={() => setAdding({ name: s.name, amount: String(s.monthly), category: s.category })}>{t("common.add")}</button>} />
            ))}
          </div>
        </>
      )}
      <Sheet open={!!adding} onClose={() => setAdding(null)} title={t("plan.addFixed")}>
        {adding && <FixedForm initial={adding} currency={data.currency} onDone={() => { setAdding(null); load(); }} />}
      </Sheet>
      <Sheet open={!!editing} onClose={() => setEditing(null)} title={editing?.name}>
        {editing && <FixedEdit item={editing} onDone={() => { setEditing(null); load(); notify(t("docs.saved")); }} />}
      </Sheet>
    </Glass>
  );
}

function FixedForm({ initial, currency, onDone }: { initial: { name: string; amount: string; category: string }; currency: string; onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [name, setName] = useState(initial.name);
  const [amount, setAmount] = useState(initial.amount);
  const [frequency, setFrequency] = useState("monthly");
  const [category, setCategory] = useState(initial.category);
  const [due, setDue] = useState("");
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try { await api.addFixed({ name, amount: amount.replace(",", "."), currency, frequency, category, next_due: due || null, match: initial.name.toLowerCase() }); onDone(); }
    catch (err) { setError(errorText(err)); }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <Field label={t("accounts.name")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} required /></Field>
      <div className="two">
        <Field label={`${t("movements.amount")} (${currency})`}><input className="field num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required /></Field>
        <Field label={t("invest.frequency")}><select className="field" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          {["weekly", "monthly", "quarterly", "yearly"].map((f) => <option key={f} value={f}>{t(`invest.freq.${f}` as never)}</option>)}</select></Field>
      </div>
      <div className="two">
        <Field label={t("movements.category")}><select className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">{t("category.none")}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(t, c)}</option>)}</select></Field>
        <Field label={`${t("plan.nextDue")} (${t("common.optional")})`}><input className="field" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
      </div>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("common.save")}</button>
    </form>
  );
}

/** Edit a fixed cost, or split one payment into its parts (a server monthly and a domain
 *  yearly, paid together): each part keeps its own amount and frequency. */
function FixedEdit({ item, onDone }: { item: FixedItem; onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [name, setName] = useState(item.name);
  const [amount, setAmount] = useState(item.amount);
  const [frequency, setFrequency] = useState(item.frequency);
  const [category, setCategory] = useState(item.category);
  const [due, setDue] = useState(item.next_due ?? "");
  const [splitting, setSplitting] = useState(false);
  const [parts, setParts] = useState([{ name: item.name, amount: item.amount, frequency: item.frequency }, { name: "", amount: "", frequency: "yearly" }]);
  const [error, setError] = useState("");
  const freqs = ["weekly", "monthly", "quarterly", "yearly"];
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      if (splitting) await api.splitFixed(item.id, parts.filter((p) => p.name && p.amount).map((p) => ({ ...p, amount: p.amount.replace(",", "."), next_due: due || null })));
      else await api.updateFixed(item.id, { name, amount: amount.replace(",", "."), currency: item.currency, frequency, category, next_due: due || null, match: item.match, active: item.active });
      onDone();
    } catch (err) { setError(errorText(err)); }
  };
  const setPart = (i: number, key: "name" | "amount" | "frequency", value: string) => setParts((old) => old.map((p, j) => (j === i ? { ...p, [key]: value } : p)));
  return (
    <form className="stack" onSubmit={save}>
      {!splitting ? (<>
        <Field label={t("accounts.name")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} required /></Field>
        <div className="two">
          <Field label={`${t("movements.amount")} (${item.currency})`}><input className="field num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required /></Field>
          <Field label={t("invest.frequency")}><select className="field" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {freqs.map((f) => <option key={f} value={f}>{t(`invest.freq.${f}` as never)}</option>)}</select></Field>
        </div>
        <div className="two">
          <Field label={t("movements.category")}><select className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">{t("category.none")}</option>{CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(t, c)}</option>)}</select></Field>
          <Field label={`${t("plan.nextDue")} (${t("common.optional")})`}><input className="field" type="date" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
        </div>
        <button type="button" className="btn ghost" onClick={() => setSplitting(true)}><Icon name="exchange" size={16} /> {t("plan.split")}</button>
      </>) : (<>
        <p className="muted small-text" style={{ margin: 0 }}>{t("plan.splitHint", { amount: item.amount })}</p>
        {parts.map((p, i) => (
          <div key={i} className="split-part">
            <input className="field" placeholder={t("accounts.name")} value={p.name} onChange={(e) => setPart(i, "name", e.target.value)} />
            <input className="field num" inputMode="decimal" placeholder="0.00" value={p.amount} onChange={(e) => setPart(i, "amount", e.target.value)} />
            <select className="field" value={p.frequency} onChange={(e) => setPart(i, "frequency", e.target.value)}>
              {freqs.map((f) => <option key={f} value={f}>{t(`invest.freq.${f}` as never)}</option>)}</select>
          </div>
        ))}
        <div className="row" style={{ gap: 8 }}>
          <button type="button" className="btn ghost small" onClick={() => setParts((old) => [...old, { name: "", amount: "", frequency: "monthly" }])}><Icon name="plus" size={14} /> {t("plan.addPart")}</button>
          <button type="button" className="btn ghost small" onClick={() => setSplitting(false)}>{t("common.back")}</button>
        </div>
      </>)}
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("common.save")}</button>
    </form>
  );
}

export function RiskCard({ onSaved }: { onSaved: () => void }) {
  const { t } = useI18n();
  const [risk, setRisk] = useState<Risk | null>(null);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [horizon, setHorizon] = useState(10);
  const [goals, setGoals] = useState("");
  const [open, setOpen] = useState(false);
  useEffect(() => { api.risk().then((r) => { setRisk(r); setAnswers(r.answers); setHorizon(r.horizon_years); setGoals(r.goals); }).catch(() => undefined); }, []);
  if (!risk) return null;
  const save = async () => { const r = await api.setRisk({ answers, horizon_years: horizon, goals }); setRisk(r); setOpen(false); onSaved(); };
  return (
    <Glass className="card">
      <h3>{t("plan.riskTitle")}</h3>
      {risk.profile ? (
        <div className="risk-meter">
          <div className="risk-bar">{[1, 2, 3, 4, 5].map((n) => <motion.i key={n} className={n <= (risk.score ?? 0) ? "on" : ""} initial={{ scaleY: 0 }} animate={{ scaleY: 1 }} transition={{ delay: n * 0.06 }} />)}</div>
          <strong>{t(`plan.profile.${risk.profile}` as never)}</strong>
          <span className="muted small-text">{t("plan.equityHint", { share: Math.round((risk.equity_share ?? 0) * 100), years: risk.horizon_years })}</span>
        </div>
      ) : <p className="muted">{t("plan.riskEmpty")}</p>}
      <button className="btn ghost" onClick={() => setOpen(true)}>{risk.profile ? t("plan.riskRedo") : t("plan.riskStart")}</button>
      <Sheet open={open} onClose={() => setOpen(false)} title={t("plan.riskTitle")}>
        <div className="stack">
          {risk.questions.map((q) => (
            <div key={q} className="question">
              <strong>{t(`plan.q.${q}` as never)}</strong>
              <div className="answers">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button key={n} className={`chip ${answers[q] === n ? "on" : ""}`} onClick={() => setAnswers({ ...answers, [q]: n })}>{t(`plan.a.${q}.${n}` as never)}</button>
                ))}
              </div>
            </div>
          ))}
          <Field label={t("plan.horizon", { n: horizon })}><input type="range" className="range" min={1} max={40} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} /></Field>
          <Field label={t("plan.goals")}><textarea className="field" rows={3} value={goals} onChange={(e) => setGoals(e.target.value)} placeholder={t("plan.goalsPlaceholder")} /></Field>
          <button className="btn big-btn" onClick={save} disabled={risk.questions.some((q) => !answers[q])}>{t("common.save")}</button>
        </div>
      </Sheet>
    </Glass>
  );
}

export const planRise = rise;
