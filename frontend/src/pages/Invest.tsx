/* Investments: what you hold (priced from the markets), your accumulation plans (PAC), and a
   simulator — the past on real prices, the future as thousands of Monte Carlo paths. */

import { motion } from "framer-motion";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Account, type Holdings, type PlanItem, type PlanProposal, type SearchHit, type Simulation } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { FanChart, GrowthChart } from "../ui/charts";
import { Empty, Field, Money, PageHeader, Row, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { Icon, Tile } from "../ui/icons";

const KIND_TILES: Record<string, [string, string, string]> = {
  stock: ["investment", "#7ea5ff", "#4a63e0"], etf: ["investments", "#6ee7d0", "#13897e"],
  bond: ["checking", "#c4c4d4", "#7f7f97"], fund: ["savings", "#ffb8d6", "#e66597"],
  crypto: ["crypto", "#ffc46b", "#e07a12"], index: ["investments", "#a08bff", "#6a4fe6"], other: ["other", "#b3aec2", "#7c7690"],
};
const kindTile = (k: string, size = 40) => { const [i, a, b] = KIND_TILES[k] ?? KIND_TILES.other; return <Tile icon={i} from={a} to={b} size={size} />; };

export function Invest({ notify, currency }: { notify: (s: string) => void; currency: string }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [holdings, setHoldings] = useState<Holdings | null>(null);
  const [plans, setPlans] = useState<PlanItem[]>([]);
  const [adding, setAdding] = useState<"holding" | "plan" | null>(null);
  const [proposed, setProposed] = useState<PlanProposal[]>([]);
  const [preset, setPreset] = useState<Partial<PlanItem> | null>(null);
  const [editing, setEditing] = useState<PlanItem | null>(null);
  const [grouping, setGrouping] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [simFor, setSimFor] = useState<{ symbol: string; monthly: number } | null>(null);
  const load = useCallback(() => {
    api.holdings().then(setHoldings).catch((e) => notify(errorText(e)));
    api.plans().then(setPlans).catch(() => undefined);
    api.planProposals().then(setProposed).catch(() => undefined);
    api.accounts().then(setAccounts).catch(() => undefined);
  }, [notify, errorText]);
  const accept = (p: PlanProposal) => api.addPlan({
    symbol: p.symbol ?? "", name: p.name ?? "", amount: p.amount, currency: p.currency, frequency: "monthly",
    start: p.start ?? new Date().toISOString().slice(0, 10), active: true, account_id: p.account_id, day: p.day, end: null,
  });
  const openPlan = (p: Partial<PlanItem> | null, edit: PlanItem | null = null) => { setPreset(p); setEditing(edit); setAdding("plan"); };
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const amount = params.get("plan");
    if (!amount) return;
    openPlan({ amount, day: Number(params.get("day")) || null, account_id: Number(params.get("account")) || null });
    setParams({}, { replace: true });
  }, [params, setParams]);
  useEffect(load, [load]);
  const cur = holdings?.currency ?? currency;

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("invest.title")} subtitle={holdings ? <span className="num">{fmt.money(holdings.total, cur)}</span> : undefined}
        action={<button className="btn" onClick={() => setAdding("holding")}><Icon name="plus" size={18} /> <span className="hide-xs">{t("invest.add")}</span></button>} />

      <div className="grid">
        <motion.div variants={rise} className="span-7">
          <Glass refract className="card">
            <h3>{t("invest.holdings")}</h3>
            {!holdings ? <Spinner /> : holdings.items.length === 0 ? (
              <Empty icon="investments" title={t("invest.empty")} hint={t("invest.emptyHint")} action={<button className="btn" onClick={() => setAdding("holding")}>{t("invest.add")}</button>} />
            ) : (
              <>
                <div className="stat-row">
                  <div><span className="muted small-text">{t("invest.value")}</span><div className="mid"><Money value={holdings.total} currency={cur} /></div></div>
                  {holdings.gain !== null && (
                    <div><span className="muted small-text">{t("invest.gain")}</span>
                      <div className={`mid ${holdings.gain >= 0 ? "up" : "down"}`}>{fmt.money(holdings.gain, cur, { sign: true })}</div></div>
                  )}
                </div>
                <div className="list">
                  {holdings.items.map((h) => (
                    <Row key={h.id} icon={kindTile(h.kind)} title={h.name || h.symbol}
                      detail={<span className="num">{h.symbol} · {fmt.number(Number(h.quantity), 6)} × {h.price ? fmt.money(h.price, h.currency) : "—"}{h.stale ? " ·" : ""}</span>}
                      right={<span className="col-right"><span className="num strong">{fmt.money(h.value, cur)}</span>
                        {h.gain !== null && <span className={`num small-text ${h.gain >= 0 ? "up" : "down"}`}>{fmt.money(h.gain, cur, { sign: true })}</span>}</span>}
                      onClick={async () => { if (confirm(`${t("common.delete")} ${h.symbol}? ${t("common.confirmDelete")}`)) { await api.deleteHolding(h.id); load(); } }} />
                  ))}
                </div>
              </>
            )}
          </Glass>
        </motion.div>

        <motion.div variants={rise} className="span-5">
          <Glass className="card">
            <div className="row between"><h3>{t("invest.plans")}</h3><button className="btn ghost small" onClick={() => setGrouping(true)}><Icon name="plus" size={16} /> {t("invest.newPlan")}</button></div>
            {proposed.length > 0 && (
              <div className="found-plans">
                <div className="row between"><span className="row" style={{ gap: 8 }}><Icon name="sparkle" size={18} /> <strong>{t("invest.found", { count: proposed.length })}</strong></span>
                  {proposed.some((p) => p.kind === "ready") && <button className="btn small" onClick={async () => {
                    for (const p of proposed.filter((x) => x.kind === "ready")) await accept(p);
                    load(); notify(t("invest.added"));
                  }}>{t("invest.addAll")}</button>}</div>
                {proposed.map((p, i) => (
                  <div key={i} className="row between found-plan">
                    <span className="row-text"><span className="row-title">{p.kind === "ready" ? p.name : t("invest.unknownSecurity")}</span>
                      <span className="row-detail">{fmt.money(p.amount, p.currency)} · {t("invest.onDay", { day: p.day })} · {p.account}{p.purchases ? ` · ${t("invest.purchasesSeen", { count: p.purchases })}` : ""}</span></span>
                    {p.kind === "ready"
                      ? <button className="btn ghost small" onClick={async () => { await accept(p); load(); notify(t("invest.added")); }}>{t("common.add")}</button>
                      : <button className="btn ghost small" onClick={() => openPlan({ amount: p.amount, day: p.day, account_id: p.account_id })}>{t("invest.pickSecurity")}</button>}
                  </div>
                ))}
              </div>
            )}
            {plans.length === 0 ? <p className="muted">{t("invest.plansEmpty")}</p> : (
              <div className="list">
                {plans.map((p) => (
                  <Row key={p.id} icon={<Tile icon="sync" from="#7ff0c9" to="#0f9e7c" size={36} />} title={p.name || p.symbol}
                    onClick={() => openPlan(p, p)}
                    detail={[`${fmt.money(p.amount, p.currency)} · ${t(`invest.freq.${p.frequency}` as never)}${p.day ? ` · ${t("invest.onDay", { day: p.day })}` : ""}`,
                      p.end ? t("invest.until", { date: fmt.date(p.end, { month: "short", year: "numeric" }) }) : t("invest.since", { date: fmt.date(p.start, { month: "short", year: "numeric" }) }),
                      p.position?.executions ? t("invest.executions", { count: p.position.executions, units: p.position.units ? fmt.number(Number(p.position.units), 4) : "—" })
                        : p.account_id ? t("invest.noExecutions") : t("invest.noAccount")].join(" · ")}
                    right={<span className="row" style={{ gap: 4 }}>
                      <button className="btn ghost small" onClick={() => setSimFor({ symbol: p.symbol, monthly: Number(p.amount) })}>{t("invest.simulate")}</button>
                      <button className="icon-btn" aria-label={t("common.delete")} onClick={async () => { await api.deletePlan(p.id); load(); }}><Icon name="trash" size={16} /></button>
                    </span>} />
                ))}
              </div>
            )}
          </Glass>
        </motion.div>

        <motion.div variants={rise} className="span-12">
          <Simulator currency={cur} preset={simFor} />
        </motion.div>
      </div>

      <Sheet open={adding === "holding"} onClose={() => setAdding(null)} title={t("invest.add")}><AddHolding onDone={() => { setAdding(null); load(); }} /></Sheet>
      <Sheet open={grouping} onClose={() => setGrouping(false)} title={t("invest.newPlan")}>
        {grouping && <PlanGroup currency={cur} accounts={accounts} onDone={() => { setGrouping(false); load(); notify(t("invest.added")); }} />}
      </Sheet>
      <Sheet open={adding === "plan"} onClose={() => setAdding(null)} title={editing ? editing.name || editing.symbol : t("invest.addPlan")}><AddPlan key={`${editing?.id ?? "new"}-${preset?.amount ?? ""}`} currency={cur} accounts={accounts} preset={preset} editing={editing} onDone={() => { setAdding(null); load(); }} /></Sheet>
    </motion.div>
  );
}

function SymbolSearch({ onPick }: { onPick: (hit: SearchHit) => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); return; }
    const id = setTimeout(() => { setBusy(true); api.search(q).then(setHits).catch(() => setHits([])).finally(() => setBusy(false)); }, 300);
    return () => clearTimeout(id);
  }, [q]);
  return (
    <div className="stack">
      <Glass variant="pill" className="search"><Icon name="search" size={18} className="faint" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("invest.searchPlaceholder")} autoFocus />{busy && <Spinner />}</Glass>
      <div className="list">
        {hits.map((h) => <Row key={h.symbol} icon={kindTile(h.kind, 34)} title={h.name} detail={`${h.symbol} · ${h.exchange}`} onClick={() => onPick(h)} chevron />)}
        {q.trim().length >= 2 && !busy && (
          <Row icon={kindTile("other", 34)} title={t("invest.notListed", { name: q.trim() })} detail={t("invest.notListedHint")}
            onClick={() => onPick({ symbol: `CUSTOM:${q.trim().toUpperCase().replace(/[^A-Z0-9]+/g, "-").slice(0, 30)}`, name: q.trim(), kind: "other", exchange: "" })} chevron />
        )}
      </div>
    </div>
  );
}

/** «From December, Anthropic instead of Nvidia»: the plan goes on with another security. */
function ReplaceSecurity({ plan, onDone }: { plan: PlanItem; onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const next = new Date(); next.setMonth(next.getMonth() + 1, 1);
  const [open, setOpen] = useState(false);
  const [hit, setHit] = useState<SearchHit | null>(null);
  const [from, setFrom] = useState(next.toISOString().slice(0, 10));
  const [error, setError] = useState("");
  if (!open) return <button type="button" className="btn ghost" onClick={() => setOpen(true)}><Icon name="exchange" size={16} /> {t("invest.replace")}</button>;
  if (!hit) return <SymbolSearch onPick={setHit} />;
  return (
    <div className="stack replace-box">
      <p style={{ margin: 0 }}>{t("invest.replaceWith", { old: plan.name || plan.symbol, new: hit.name })}</p>
      <Field label={t("invest.replaceFrom")}><input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
      {error && <div className="error">{error}</div>}
      <button type="button" className="btn" onClick={async () => {
        try { await api.replacePlan(plan.id, { symbol: hit.symbol, name: hit.name, start: from }); onDone(); } catch (e) { setError(errorText(e)); }
      }}>{t("invest.replaceConfirm")}</button>
    </div>
  );
}

/** One savings plan of several securities: account, day, start, and how much each gets. */
function PlanGroup({ currency, accounts, onDone }: { currency: string; accounts: Account[]; onDone: () => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [account, setAccount] = useState<number | "">(accounts.find((a) => /trade republic|scalable|directa|degiro/i.test(`${a.institution} ${a.name}`))?.id ?? "");
  const [day, setDay] = useState("16");
  const [start, setStart] = useState(new Date().toISOString().slice(0, 10));
  const [items, setItems] = useState<{ hit: SearchHit; amount: string }[]>([]);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const total = items.reduce((sum, i) => sum + (Number(i.amount.replace(",", ".")) || 0), 0);
  if (picking) return <SymbolSearch onPick={(hit) => { setItems((old) => [...old, { hit, amount: "100" }]); setPicking(false); }} />;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.addPlans({ account_id: account || null, day: day ? Number(day) : null, start, frequency: "monthly", currency,
        items: items.map((i) => ({ symbol: i.hit.symbol, name: i.hit.name, amount: i.amount.replace(",", ".") })) });
      onDone();
    } catch (err) { setError(errorText(err)); }
  };
  return (
    <form className="stack" onSubmit={save}>
      <div className="two">
        <Field label={t("invest.payFrom")}>
          <select className="field" value={account} onChange={(e) => setAccount(e.target.value ? Number(e.target.value) : "")}>
            <option value="">—</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Field>
        <Field label={t("invest.dayOfMonth")}><input className="field num" inputMode="numeric" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, "").slice(0, 2))} /></Field>
      </div>
      <Field label={t("invest.start")}><input className="field" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
      <h4 className="section-label">{t("invest.securities")}</h4>
      {items.map((it, i) => (
        <div key={i} className="row" style={{ gap: 8 }}>
          {kindTile(it.hit.kind, 32)}
          <span className="row-text" style={{ flex: 1 }}><span className="row-title ellipsis">{it.hit.name}</span><span className="row-detail">{it.hit.symbol.startsWith("CUSTOM:") ? t("invest.unlisted") : it.hit.symbol}</span></span>
          <input className="field num" style={{ width: 100 }} inputMode="decimal" value={it.amount} onChange={(e) => setItems((old) => old.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} />
          <button type="button" className="icon-btn" aria-label={t("common.delete")} onClick={() => setItems((old) => old.filter((_, j) => j !== i))}><Icon name="trash" size={16} /></button>
        </div>
      ))}
      <button type="button" className="btn ghost" onClick={() => setPicking(true)}><Icon name="plus" size={16} /> {t("invest.addSecurity")}</button>
      {items.length > 0 && <p className="muted" style={{ margin: 0 }}>{t("invest.planTotal", { amount: fmt.money(total, currency), day: day || "—" })}</p>}
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" disabled={items.length === 0}>{t("common.add")}</button>
    </form>
  );
}

function AddHolding({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [hit, setHit] = useState<SearchHit | null>(null);
  const [manual, setManual] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState("bond");
  const [currency, setCurrency] = useState("EUR");
  const [price, setPrice] = useState("");
  const [quantity, setQuantity] = useState("");
  const [cost, setCost] = useState("");
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.addHolding(manual
        ? { symbol, name: symbol, kind, currency, quantity: quantity.replace(",", "."), cost: cost.replace(",", ".") || undefined, manual_price: price.replace(",", ".") }
        : { symbol: hit!.symbol, name: hit!.name, kind: hit!.kind, quantity: quantity.replace(",", "."), cost: cost.replace(",", ".") || undefined });
      onDone();
    } catch (err) { setError(errorText(err)); }
  };
  if (!hit && !manual) {
    return (
      <div className="stack">
        <SymbolSearch onPick={setHit} />
        <button className="link" onClick={() => setManual(true)}>{t("invest.manual")}</button>
      </div>
    );
  }
  return (
    <form className="stack" onSubmit={submit}>
      {hit ? <Row icon={kindTile(hit.kind)} title={hit.name} detail={hit.symbol} /> : (
        <>
          <Field label={t("invest.symbol")} hint={t("invest.manualHint")}><input className="field mono" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} required placeholder="IT0005436693" /></Field>
          <div className="two">
            <Field label={t("accounts.kind")}><select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>{Object.keys(KIND_TILES).map((k) => <option key={k} value={k}>{t(`invest.kind.${k}` as never)}</option>)}</select></Field>
            <Field label={t("accounts.currency")}><input className="field" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></Field>
          </div>
          <Field label={t("invest.price")}><input className="field num" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} required /></Field>
        </>
      )}
      <div className="two">
        <Field label={t("invest.quantity")}><input className="field num" inputMode="decimal" value={quantity} onChange={(e) => setQuantity(e.target.value)} required autoFocus /></Field>
        <Field label={`${t("invest.cost")} (${t("common.optional")})`}><input className="field num" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} /></Field>
      </div>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("common.add")}</button>
    </form>
  );
}

function AddPlan({ currency, accounts, preset, editing, onDone }: {
  currency: string; accounts: Account[]; preset: Partial<PlanItem> | null; editing: PlanItem | null; onDone: () => void;
}) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [hit, setHit] = useState<SearchHit | null>(editing ? { symbol: editing.symbol, name: editing.name, kind: "etf", exchange: "" } : null);
  const [amount, setAmount] = useState(preset?.amount ?? "100");
  const [frequency, setFrequency] = useState(preset?.frequency ?? "monthly");
  const [start, setStart] = useState(preset?.start ?? new Date().toISOString().slice(0, 10));
  const [day, setDay] = useState(String(preset?.day ?? ""));
  const [end, setEnd] = useState(preset?.end ?? "");
  const [account, setAccount] = useState<number | "">(preset?.account_id ?? "");
  const [error, setError] = useState("");
  if (!hit) return <SymbolSearch onPick={setHit} />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body = { symbol: hit.symbol, name: hit.name, amount: amount.replace(",", "."), currency, frequency, start, active: true,
      account_id: account || null, day: day ? Number(day) : null, end: end || null };
    try {
      if (editing) await api.updatePlan(editing.id, body); else await api.addPlan(body);
      onDone();
    } catch (err) { setError(errorText(err)); }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <Row icon={kindTile(hit.kind)} title={hit.name} detail={hit.symbol} />
      <div className="two">
        <Field label={`${t("movements.amount")} (${currency})`}><input className="field num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required /></Field>
        <Field label={t("invest.frequency")}><select className="field" value={frequency} onChange={(e) => setFrequency(e.target.value)}>
          {["weekly", "monthly", "quarterly"].map((f) => <option key={f} value={f}>{t(`invest.freq.${f}` as never)}</option>)}</select></Field>
      </div>
      <div className="two">
        <Field label={t("invest.payFrom")} hint={t("invest.payFromHint")}>
          <select className="field" value={account} onChange={(e) => setAccount(e.target.value ? Number(e.target.value) : "")}>
            <option value="">—</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution && a.institution !== a.name ? ` (${a.institution})` : ""}</option>)}
          </select>
        </Field>
        <Field label={t("invest.dayOfMonth")}><input className="field num" inputMode="numeric" value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, "").slice(0, 2))} placeholder="16" /></Field>
      </div>
      <div className="two">
        <Field label={t("invest.start")}><input className="field" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label={`${t("invest.end")} (${t("common.optional")})`}><input className="field" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
      </div>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{editing ? t("common.save") : t("common.add")}</button>
      {editing && <ReplaceSecurity plan={editing} onDone={onDone} />}
      {editing && <button type="button" className="btn ghost danger" onClick={async () => { await api.deletePlan(editing.id); onDone(); }}><Icon name="trash" size={16} /> {t("common.delete")}</button>}
    </form>
  );
}

function Simulator({ currency, preset }: { currency: string; preset: { symbol: string; monthly: number } | null }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [assets, setAssets] = useState<{ symbol: string; name: string; weight: number }[]>([]);
  const [picking, setPicking] = useState(false);
  const [initial, setInitial] = useState("0");
  const [monthly, setMonthly] = useState("200");
  const [years, setYears] = useState(15);
  const [method, setMethod] = useState("bootstrap");
  const [real, setReal] = useState(true);
  const [target, setTarget] = useState("");
  const [result, setResult] = useState<Simulation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!preset) return;
    setAssets([{ symbol: preset.symbol, name: preset.symbol, weight: 1 }]);
    setMonthly(String(preset.monthly));
    setInitial("0");
  }, [preset]);
  const run = async () => {
    if (!assets.length) { setPicking(true); return; }
    setBusy(true); setError("");
    try {
      setResult(await api.simulate({ assets: assets.map((a) => ({ symbol: a.symbol, weight: a.weight })), initial: Number(initial.replace(",", ".")) || 0,
        monthly: Number(monthly.replace(",", ".")) || 0, years, method, inflation: real ? 0.02 : 0, target: target ? Number(target) : null }));
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const mc = result?.monte_carlo;
  const m = Number(monthly.replace(",", ".")) || 0;
  const i0 = Number(initial.replace(",", ".")) || 0;
  return (
    <Glass refract className="card">
      <h3>{t("invest.simulator")}</h3>
      <p className="muted small-text" style={{ marginTop: -6 }}>{t("invest.simulatorHint")}</p>
      <div className="chips" style={{ marginBottom: 12 }}>
        {assets.map((a, i) => (
          <span key={a.symbol} className="chip on asset-chip">
            {a.symbol}
            {assets.length > 1 && <input type="number" min={1} max={100} value={a.weight} aria-label={t("invest.weight")} onChange={(e) => setAssets(assets.map((x, j) => (j === i ? { ...x, weight: Number(e.target.value) || 1 } : x)))} />}
            <button aria-label={t("common.delete")} onClick={() => setAssets(assets.filter((_, j) => j !== i))}><Icon name="close" size={13} /></button>
          </span>
        ))}
        <button className="chip" onClick={() => setPicking(true)}><Icon name="plus" size={14} /> {t("invest.addAsset")}</button>
      </div>
      <div className="sim-grid">
        <Field label={`${t("invest.initial")} (${currency})`}><input className="field num" inputMode="decimal" value={initial} onChange={(e) => setInitial(e.target.value)} /></Field>
        <Field label={`${t("invest.monthly")} (${currency})`}><input className="field num" inputMode="decimal" value={monthly} onChange={(e) => setMonthly(e.target.value)} /></Field>
        <Field label={t("invest.years", { n: years })}><input type="range" min={1} max={40} value={years} onChange={(e) => setYears(Number(e.target.value))} className="range" /></Field>
        <Field label={`${t("invest.target")} (${t("common.optional")})`}><input className="field num" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} /></Field>
      </div>
      <div className="row wrap" style={{ gap: 10, margin: "4px 0 14px" }}>
        <div className="segmented">
          {["bootstrap", "gbm"].map((x) => <button key={x} className={method === x ? "on" : ""} onClick={() => setMethod(x)}>{t(`invest.method.${x}` as never)}</button>)}
        </div>
        <label className="check"><input type="checkbox" checked={real} onChange={(e) => setReal(e.target.checked)} /> {t("invest.real")}</label>
        <button className="btn" onClick={run} disabled={busy} style={{ marginLeft: "auto" }}>{busy ? <Spinner /> : <><Icon name="sparkle" size={16} /> {t("invest.run")}</>}</button>
      </div>
      {error && <div className="error">{error}</div>}
      {result && mc && (
        <motion.div className="stack" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <div className="tiles">
            <div className="tile-stat"><span>{t("invest.median")}</span><strong className="num">{fmt.money(mc.median, currency, { compact: true })}</strong></div>
            <div className="tile-stat"><span>{t("invest.range")}</span><strong className="num">{fmt.money(mc.p5, currency, { compact: true })} – {fmt.money(mc.p95, currency, { compact: true })}</strong></div>
            <div className="tile-stat"><span>{t("invest.invested")}</span><strong className="num">{fmt.money(mc.invested, currency, { compact: true })}</strong></div>
            <div className="tile-stat"><span>{t("invest.probLoss")}</span><strong className={`num ${mc.prob_loss > 0.2 ? "down" : ""}`}>{fmt.percent(mc.prob_loss)}</strong></div>
            {mc.prob_target !== undefined && <div className="tile-stat"><span>{t("invest.probTarget")}</span><strong className="num up">{fmt.percent(mc.prob_target)}</strong></div>}
            <div className="tile-stat"><span>{t("invest.volatility")}</span><strong className="num">{fmt.percent(mc.yearly_vol)}</strong></div>
          </div>
          <h4 className="section-label">{t("invest.future")}</h4>
          <FanChart bands={mc.bands} currency={currency} invested={(y) => i0 + m * 12 * y} />
          <h4 className="section-label">{t("invest.pastTitle", { years: result.backtest.years, from: fmt.date(result.history_from, { month: "short", year: "numeric" }) })}</h4>
          <GrowthChart points={result.backtest.points} currency={currency} />
          <p className="small-text muted">{t("invest.pastSummary", {
            invested: fmt.money(result.backtest.invested, currency, { compact: true }), value: fmt.money(result.backtest.value, currency, { compact: true }),
            cagr: fmt.percent(result.backtest.asset_cagr), dd: fmt.percent(result.backtest.max_drawdown) })}</p>
          {result.backtest.years < 10 && <p className="note warn"><Icon name="none" size={18} /> {t("invest.shortHistory", { years: result.backtest.years })}</p>}
          <p className="faint small-text">{t("invest.disclaimer", { paths: mc.paths })}</p>
        </motion.div>
      )}
      <Sheet open={picking} onClose={() => setPicking(false)} title={t("invest.addAsset")}>
        <SymbolSearch onPick={(h) => { setAssets([...assets.filter((a) => a.symbol !== h.symbol), { symbol: h.symbol, name: h.name, weight: 1 }]); setPicking(false); }} />
      </Sheet>
    </Glass>
  );
}
