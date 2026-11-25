/* Every movement, grouped by day. Tap one to sort it into a category (Oink learns the
   merchant) or add a note; add cash movements by hand. */

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Account, type MovementReview, type PlanItem, type Tx } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { Empty, Field, PageHeader, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { CategoryTile, Icon } from "../ui/icons";
import { CATEGORIES, categoryLabel } from "../ui/labels";

const PAGE = 80;
type Period = "all" | "month" | "last" | "3m" | "year";
const PERIODS: Period[] = ["all", "month", "last", "3m", "year"];
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function periodRange(p: Period): { date_from?: string; date_to?: string } {
  const now = new Date();
  if (p === "month") return { date_from: iso(new Date(now.getFullYear(), now.getMonth(), 1)) };
  if (p === "last") return { date_from: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), date_to: iso(new Date(now.getFullYear(), now.getMonth(), 0)) };
  if (p === "3m") return { date_from: iso(new Date(now.getFullYear(), now.getMonth() - 2, 1)) };
  if (p === "year") return { date_from: iso(new Date(now.getFullYear(), 0, 1)) };
  return {};
}

export function Activity({ notify }: { ai: boolean; notify: (s: string) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [items, setItems] = useState<Tx[] | null>(null);
  const [more, setMore] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [q, setQ] = useState("");
  const [account, setAccount] = useState<number | undefined>();
  const [direction, setDirection] = useState<"" | "in" | "out">("");
  const [period, setPeriod] = useState<Period>("all");
  const [cats, setCats] = useState<string[]>([]);
  const [sort, setSort] = useState<"date" | "amount_desc" | "amount_asc">("date");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [base, setBase] = useState("EUR");
  useEffect(() => { api.status().then((s) => setBase(s.user?.base_currency ?? "EUR")).catch(() => undefined); }, []);
  const [selected, setSelected] = useState<Tx | null>(null);
  const [adding, setAdding] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  const load = useCallback(async (offset = 0) => {
    const range = periodRange(period);
    const page = await api.transactions({ q, account_id: account, limit: PAGE, offset, sort,
      direction: direction || undefined, categories: cats.join(",") || undefined, ...range });
    setItems((old) => (offset && old ? [...old, ...page] : page));
    setMore(page.length === PAGE);
  }, [q, account, sort, direction, cats, period]);

  useEffect(() => {
    const id = setTimeout(() => load().catch((e) => notify(errorText(e))), q ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, q, notify, errorText]);
  useEffect(() => { api.accounts().then(setAccounts).catch(() => undefined); }, []);

  const byDay = useMemo(() => {
    const groups: [string, Tx[]][] = [];
    if (sort !== "date") return items?.length ? [["", items] as [string, Tx[]]] : groups;
    for (const tx of items ?? []) {
      const last = groups[groups.length - 1];
      if (last && last[0] === tx.day) last[1].push(tx);
      else groups.push([tx.day, [tx]]);
    }
    return groups;
  }, [items, sort]);
  const active = (direction ? 1 : 0) + (period !== "all" ? 1 : 0) + cats.length + (sort !== "date" ? 1 : 0);
  const reset = () => { setDirection(""); setPeriod("all"); setCats([]); setSort("date"); };
  const totals = useMemo(() => {
    let inc = 0, out = 0;
    for (const tx of items ?? []) { const v = Number(tx.amount); if (tx.currency !== base) continue; if (v > 0) inc += v; else out += v; }
    return { inc, out };
  }, [items, base]);

  const dayLabel = (day: string) => {
    const today = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (day === today) return t("movements.today");
    if (day === yesterday) return t("movements.yesterday");
    return fmt.date(day, { weekday: "long", day: "numeric", month: "long" });
  };



  const accountName = (id: number) => accounts.find((a) => a.id === id)?.name ?? "";

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("movements.title")} action={
        <div className="row" style={{ gap: 8 }}>
          <button className="icon-btn glassy" onClick={() => setReviewing(true)} aria-label={t("review.title")}><Icon name="sparkle" /></button>
          <button className="icon-btn glassy" onClick={() => setAdding(true)} aria-label={t("movements.add")} title={t("movements.add")}><Icon name="plus" /></button>
        </div>
      } />

      <motion.div variants={rise} className="toolbar">
        <div className="row" style={{ gap: 8 }}>
          <Glass variant="pill" className="search" style={{ flex: 1 }}>
            <Icon name="search" size={18} className="faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("movements.search")} aria-label={t("common.search")} />
          </Glass>
          <button className={`icon-btn glassy filter-btn ${filtersOpen || active ? "on" : ""}`} onClick={() => setFiltersOpen((v) => !v)} aria-label={t("movements.filters")}>
            <Icon name="settings" />{active > 0 && <span className="filter-count">{active}</span>}
          </button>
        </div>
        <div className="segmented">
          {(["", "out", "in"] as const).map((d) => <button key={d} className={direction === d ? "on" : ""} onClick={() => setDirection(d)}>{t(d === "" ? "common.all" : d === "out" ? "movements.out" : "movements.in")}</button>)}
        </div>
        <AnimatePresence initial={false}>
          {filtersOpen && (
            <motion.div className="filters" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
              <div className="filter-group">
                <span className="section-label">{t("movements.period")}</span>
                <div className="chips">{PERIODS.map((p) => <button key={p} className={`chip ${period === p ? "on" : ""}`} onClick={() => setPeriod(p)}>{t(`movements.period.${p}` as never)}</button>)}</div>
              </div>
              <div className="filter-group">
                <span className="section-label">{t("movements.sortBy")}</span>
                <div className="chips">{(["date", "amount_desc", "amount_asc"] as const).map((o) => <button key={o} className={`chip ${sort === o ? "on" : ""}`} onClick={() => setSort(o)}>{t(`movements.sort.${o}` as never)}</button>)}</div>
              </div>
              {accounts.length > 1 && (
                <div className="filter-group">
                  <span className="section-label">{t("movements.account")}</span>
                  <div className="chips">
                    <button className={`chip ${account === undefined ? "on" : ""}`} onClick={() => setAccount(undefined)}>{t("common.all")}</button>
                    {accounts.map((a) => <button key={a.id} className={`chip ${account === a.id ? "on" : ""}`} onClick={() => setAccount(a.id)}>{a.name}</button>)}
                  </div>
                </div>
              )}
              <div className="filter-group">
                <span className="section-label">{t("movements.category")}</span>
                <div className="cat-pick">
                  {CATEGORIES.map((c) => (
                    <button key={c} className={`cat-chip ${cats.includes(c) ? "on" : ""}`} onClick={() => setCats((old) => old.includes(c) ? old.filter((x) => x !== c) : [...old, c])}>
                      <CategoryTile category={c} size={26} /> <span>{categoryLabel(t, c)}</span>
                    </button>
                  ))}
                </div>
              </div>
              {active > 0 && <button className="btn ghost small" onClick={reset}>{t("movements.resetFilters")}</button>}
            </motion.div>
          )}
        </AnimatePresence>
        {items && items.length > 0 && (
          <div className="sum-row">
            <span>{t("movements.shown", { count: items.length })}</span>
            <span className="num up">{fmt.money(totals.inc, base, { sign: true })}</span>
            <span className="num">{fmt.money(totals.out, base, { sign: true })}</span>
          </div>
        )}
      </motion.div>

      {items === null ? (
        <div className="skeleton" style={{ height: 400 }} />
      ) : items.length === 0 ? (
        <Glass className="card"><Empty icon="activity" title={t("movements.empty")} /></Glass>
      ) : (
        <motion.div variants={rise} className="days">
          {byDay.map(([day, txs]) => (
            <section key={day}>
              {day && <h4 className="day-head cap row between"><span>{dayLabel(day)}</span>
                {txs[0].total_after != null && account === undefined && <span className="num faint">{fmt.money(txs[0].total_after, base)}</span>}
                {txs[0].balance_after != null && account !== undefined && <span className="num faint">{fmt.money(txs[0].balance_after, txs[0].currency)}</span>}</h4>}
              <Glass className="card list tight-card">
                {txs.map((tx) => (
                  <button key={tx.id} className="item press-row" onClick={() => setSelected(tx)}>
                    <CategoryTile category={tx.category} />
                    <span className="item-main">
                      <span className="row-text">
                        <span className="row-title ellipsis">{tx.counterparty || tx.description}</span>
                        <span className="row-detail ellipsis">{tx.source === "pending" ? `${t("movements.pending")} · ` : ""}{categoryLabel(t, tx.category)}{accounts.length > 1 ? ` · ${accountName(tx.account_id)}` : ""}</span>
                      </span>
                      <span className="col-right">
                        <span className={`num amount ${Number(tx.amount) > 0 ? "up" : ""}`}>{fmt.money(tx.amount, tx.currency, { sign: Number(tx.amount) > 0 })}</span>
                        {sort !== "date" ? <span className="num small-text faint">{fmt.date(tx.day, { day: "numeric", month: "short" })}</span>
                          : (account === undefined ? tx.total_after != null && <span className="num small-text faint">{fmt.money(tx.total_after, base)}</span>
                            : tx.balance_after != null && <span className="num small-text faint">{fmt.money(tx.balance_after, tx.currency)}</span>)}
                      </span>
                    </span>
                  </button>
                ))}
              </Glass>
            </section>
          ))}
          {more && <button className="btn ghost center-btn" onClick={() => load(items.length)}>{t("movements.loadMore")}</button>}
        </motion.div>
      )}

      <TxSheet tx={selected} onClose={() => setSelected(null)} onSaved={(tx) => { setItems((old) => old?.map((x) => (x.id === tx.id ? tx : x)) ?? null); setSelected(null); notify(t("movements.saved")); }}
        onDeleted={(id) => { setItems((old) => old?.filter((x) => x.id !== id) ?? null); setSelected(null); }} />
      <Sheet open={reviewing} onClose={() => setReviewing(false)} title={t("review.title")} wide>
        {reviewing && <ReviewWizard onDone={(n) => { setReviewing(false); load(); notify(t("review.applied", { count: n })); }} />}
      </Sheet>
      <AddSheet open={adding} accounts={accounts} onClose={() => setAdding(false)} onAdded={() => { setAdding(false); load(); notify(t("movements.saved")); }} />
    </motion.div>
  );
}

function TxSheet({ tx, onClose, onSaved, onDeleted }: { tx: Tx | null; onClose: () => void; onSaved: (tx: Tx) => void; onDeleted: (id: number) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [category, setCategory] = useState("");
  const [custom, setCustom] = useState("");
  const [note, setNote] = useState("");
  const [learn, setLearn] = useState(true);
  const [allCats, setAllCats] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!tx) return;
    setCategory(tx.category);
    setCustom(tx.category && !CATEGORIES.includes(tx.category) ? tx.category : "");
    setNote(tx.note);
    setName(tx.counterparty || tx.description);
    setError("");
  }, [tx]);
  if (!tx) return <Sheet open={false} onClose={onClose}>{null}</Sheet>;

  const save = async () => {
    try {
      const renamed = name.trim() && name.trim() !== (tx.counterparty || tx.description) ? name.trim() : undefined;
      onSaved(await api.patchTx(tx.id, { category: custom.trim() || category, note, learn, counterparty: renamed }));
    } catch (e) {
      setError(errorText(e));
    }
  };
  const remove = async () => {
    if (!confirm(t("common.confirmDelete"))) return;
    await api.deleteTx(tx.id);
    onDeleted(tx.id);
  };

  return (
    <Sheet open={!!tx} onClose={onClose} title={tx.counterparty || tx.description}>
      <div className="tx-hero">
        <CategoryTile category={custom.trim() || category} size={56} />
        <div className={`big-amount num ${Number(tx.amount) > 0 ? "up" : ""}`}>{fmt.money(tx.amount, tx.currency, { sign: Number(tx.amount) > 0 })}</div>
        <div className="muted">{fmt.date(tx.day, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</div>
        {tx.counterparty && tx.description !== tx.counterparty && <div className="faint small-text">{tx.description}</div>}
        {tx.category_by === "transfer" && <div className="pill-note">{tx.transfer_id ? t("transfers.paired") : t("transfers.untracked")}</div>}
      </div>
      <FromTo tx={tx} />
      {Number(tx.amount) < 0 && <MarkAs tx={tx} onDone={onSaved} />}
      <div className="row between"><h4 className="section-label">{t("movements.category")}</h4>
        <button className="btn ghost small" onClick={() => setAllCats((v) => !v)}>{allCats ? t("plan.showLess") : t("movements.allCategories")}</button></div>
      <div className={`cat-grid ${allCats ? "" : "compact"}`}>
        {(allCats ? CATEGORIES : [category || "other", ...CATEGORIES.filter((c) => c !== (category || "other"))].slice(0, 6)).map((c) => (
          <button key={c} className={`cat ${category === c && !custom ? "on" : ""}`} onClick={() => { setCategory(c); setCustom(""); }}>
            <CategoryTile category={c} size={44} />
            <span>{categoryLabel(t, c)}</span>
          </button>
        ))}
      </div>
      <div className="stack">
        <input className="field" placeholder={t("movements.customCategory")} value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={40} />
        <label className="check"><input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} /> {t("movements.learn")}</label>
        <Field label={t("movements.payee")} hint={t("movements.payeeHint")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} /></Field>
        <Field label={t("movements.note")}><textarea className="field" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {error && <div className="error">{error}</div>}
        <div className="row between">
          <button className="btn ghost danger" onClick={remove}><Icon name="trash" size={18} /> {t("common.delete")}</button>
          <button className="btn" onClick={save}>{t("common.save")}</button>
        </div>
      </div>
    </Sheet>
  );
}

function AddSheet({ open, accounts, onClose, onAdded }: { open: boolean; accounts: Account[]; onClose: () => void; onAdded: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [accountId, setAccountId] = useState<number | "">("");
  const [amount, setAmount] = useState("");
  const [description, setDescription] = useState("");
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10));
  const [category, setCategory] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { if (open && accounts.length && accountId === "") setAccountId(accounts[0].id); }, [open, accounts, accountId]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (accountId === "") return;
    try {
      await api.addTx({ account_id: accountId, day, amount: amount.replace(",", "."), description, category });
      setAmount(""); setDescription(""); setCategory(""); setError("");
      onAdded();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title={t("movements.add")}>
      <form className="stack" onSubmit={submit}>
        <Field label={t("movements.account")}>
          <select className="field" value={accountId} onChange={(e) => setAccountId(Number(e.target.value))} required>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.currency})</option>)}
          </select>
        </Field>
        <Field label={t("movements.amount")} hint={t("movements.amountHint")}>
          <input className="field num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} required placeholder="-12.50" />
        </Field>
        <Field label={t("movements.description")}><input className="field" value={description} onChange={(e) => setDescription(e.target.value)} required /></Field>
        <div className="two">
          <Field label={t("movements.date")}><input className="field" type="date" value={day} onChange={(e) => setDay(e.target.value)} required /></Field>
          <Field label={t("movements.category")}>
            <select className="field" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">{t("category.none")}</option>
              {CATEGORIES.map((c) => <option key={c} value={c}>{categoryLabel(t, c)}</option>)}
            </select>
          </Field>
        </div>
        <AnimatePresence>{error && <motion.div className="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>{error}</motion.div>}</AnimatePresence>
        <button className="btn big-btn">{t("common.add")}</button>
      </form>
    </Sheet>
  );
}

/** «This is…»: a purchase of a savings plan, or a fixed cost. One tap, filled in from the
 *  movement; the plan learns its account and day, so its other months follow by themselves. */
function MarkAs({ tx, onDone }: { tx: Tx; onDone: (tx: Tx) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const navigate = useNavigate();
  const [plans, setPlans] = useState<PlanItem[] | null>(null);
  const [mode, setMode] = useState<"" | "plan">("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.plans().then(setPlans).catch(() => setPlans([])); }, [tx.id]);
  const linked = plans?.find((p) => p.id === tx.plan_id);
  const amount = Math.abs(Number(tx.amount)).toFixed(2);
  const asPlan = async (planId: number) => {
    try { await api.markPurchase(tx.id, planId); onDone({ ...tx, plan_id: planId, category: "investments", category_by: "plan" }); }
    catch (e) { setMsg(errorText(e)); }
  };
  const asFixed = async () => {
    const next = new Date(tx.day); next.setMonth(next.getMonth() + 1);
    try {
      await api.addFixed({ name: tx.counterparty || tx.description, amount, currency: tx.currency, frequency: "monthly",
        category: tx.category || undefined, next_due: next.toISOString().slice(0, 10), match: (tx.counterparty || tx.description).slice(0, 60) });
      setMsg(t("movements.fixedAdded"));
    } catch (e) { setMsg(errorText(e)); }
  };
  if (linked) return (
    <div className="mark-as">
      <span className="pill-note">{t("movements.isPurchase", { plan: linked.name || linked.symbol })}</span>
      <button className="btn ghost small" onClick={async () => { await api.unmarkPurchase(tx.id); onDone({ ...tx, plan_id: null, category: "", category_by: "user" }); }}>{t("movements.notPurchase")}</button>
    </div>
  );
  return (
    <div className="mark-as">
      <h4 className="section-label">{t("movements.markAs")}</h4>
      <div className="row wrap" style={{ gap: 8 }}>
        <button className={`chip ${mode === "plan" ? "on" : ""}`} onClick={() => setMode(mode === "plan" ? "" : "plan")}><Icon name="investments" size={15} /> {t("movements.asPlan")}</button>
        <button className="chip" onClick={asFixed}><Icon name="bills" size={15} /> {t("movements.asFixed")}</button>
      </div>
      {mode === "plan" && plans && (
        <div className="list" style={{ marginTop: 8 }}>
          {plans.map((p) => (
            <button key={p.id} className="item press-row" onClick={() => asPlan(p.id)}>
              <span className="item-main"><span className="row-text"><span className="row-title">{p.name || p.symbol}</span>
                <span className="row-detail">{fmt.money(p.amount, p.currency)} · {p.symbol}</span></span></span>
            </button>
          ))}
          <button className="item press-row" onClick={() => navigate(`/invest?plan=${amount}&day=${new Date(tx.day).getDate()}&account=${tx.account_id}`)}>
            <span className="item-main"><span className="row-text"><span className="row-title">{t("movements.newPlan")}</span>
              <span className="row-detail">{t("movements.newPlanHint", { amount: fmt.money(amount, tx.currency) })}</span></span></span>
          </button>
        </div>
      )}
      {msg && <p className="muted small-text" style={{ margin: "6px 0 0" }}>{msg}</p>}
    </div>
  );
}

/** Who paid whom: the account and the payee, or the two own accounts of a transfer. */
function FromTo({ tx }: { tx: Tx }) {
  const { t } = useI18n();
  const [accounts, setAccounts] = useState<Account[]>([]);
  useEffect(() => { api.accounts().then(setAccounts).catch(() => undefined); }, []);
  const mine = accounts.find((a) => a.id === tx.account_id);
  const partner = accounts.find((a) => a.id === tx.partner_account_id);
  const other = partner ? partner.name : (tx.counterparty || (tx.description !== "—" ? tx.description : "") || t("movements.unknownParty"));
  const label = (a?: Account) => (a ? `${a.name}${a.institution && a.institution !== a.name ? ` · ${a.institution}` : ""}` : "—");
  const out = Number(tx.amount) < 0;
  return (
    <div className="from-to">
      <div><span className="faint small-text">{t("movements.from")}</span><strong>{out ? label(mine) : partner ? label(partner) : other}</strong></div>
      <Icon name="chevron" size={18} className="faint" />
      <div><span className="faint small-text">{t("movements.to")}</span><strong>{out ? (partner ? label(partner) : other) : label(mine)}</strong></div>
    </div>
  );
}

type Range = "all" | "month" | "quarter" | "half" | "custom";

/** Look at a period's movements and propose what to fix: savings plans, fixed costs,
 *  categories (by payee, account and recurrence; the AI only for the rest). */
function ReviewWizard({ onDone }: { onDone: (applied: number) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [range, setRange] = useState<Range>("month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [result, setResult] = useState<MovementReview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [skip, setSkip] = useState<Set<string>>(new Set());
  const dates = (): { date_from?: string; date_to?: string } => {
    const now = new Date();
    if (range === "month") return { date_from: iso(new Date(now.getFullYear(), now.getMonth(), 1)) };
    if (range === "quarter") return { date_from: iso(new Date(now.getFullYear(), now.getMonth() - 2, 1)) };
    if (range === "half") return { date_from: iso(new Date(now.getFullYear(), now.getMonth() - 5, 1)) };
    if (range === "custom") return { date_from: from || undefined, date_to: to || undefined };
    return {};
  };
  const run = async () => {
    setBusy(true); setError("");
    try { setResult(await api.review(dates())); setSkip(new Set()); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const toggle = (key: string) => setSkip((old) => { const n = new Set(old); if (n.has(key)) n.delete(key); else n.add(key); return n; });
  const apply = async () => {
    if (!result) return;
    setBusy(true);
    let n = 0;
    try {
      for (const [i, p] of result.plans.entries()) if (p.kind === "ready" && !skip.has(`p${i}`)) {
        await api.addPlan({ symbol: p.symbol ?? "", name: p.name ?? "", amount: p.amount, currency: p.currency, frequency: "monthly",
          start: p.start ?? iso(new Date()), active: true, account_id: p.account_id, day: p.day, end: null }); n++;
      }
      for (const [i, f] of result.fixed.entries()) if (!skip.has(`f${i}`)) {
        await api.addFixed({ name: f.name, amount: f.amount, currency: f.currency, frequency: f.frequency, category: f.category, match: f.match }); n++;
      }
      for (const c of result.categories) if (!skip.has(`c${c.tx_id}`)) { await api.patchTx(c.tx_id, { category: c.proposed, learn: true }); n++; }
      onDone(n);
    } catch (e) { setError(errorText(e)); setBusy(false); }
  };
  const ranges: Range[] = ["month", "quarter", "half", "all", "custom"];
  if (!result) return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>{t("review.intro")}</p>
      <div className="chips wrap-chips">{ranges.map((r) => <button key={r} className={`chip ${range === r ? "on" : ""}`} onClick={() => setRange(r)}>{t(`review.range.${r}` as never)}</button>)}</div>
      {range === "custom" && <div className="two">
        <Field label={t("review.from")}><input className="field" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
        <Field label={t("review.to")}><input className="field" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
      </div>}
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" onClick={run} disabled={busy}>{busy ? <><Spinner /> {t("review.running")}</> : t("review.run")}</button>
    </div>
  );
  const total = result.plans.filter((p) => p.kind === "ready").length + result.fixed.length + result.categories.length;
  const picked = total - skip.size;
  return (
    <div className="stack">
      <p className="muted" style={{ margin: 0 }}>{t("review.checked", { count: result.checked })}</p>
      {total === 0 && <p className="note"><Icon name="check" size={18} /> {t("review.nothing")}</p>}
      {result.plans.length > 0 && <section className="review-section">
        <h4 className="section-label">{t("review.plans")}</h4>
        {result.plans.map((p, i) => (
          <label key={i} className="review-row">
            {p.kind === "ready" ? <input type="checkbox" checked={!skip.has(`p${i}`)} onChange={() => toggle(`p${i}`)} /> : <span style={{ width: 18 }} />}
            <span className="row-text"><span className="row-title">{p.kind === "ready" ? p.name : t("invest.unknownSecurity")}</span>
              <span className="row-detail">{fmt.money(p.amount, p.currency)} · {t("invest.onDay", { day: p.day })} · {p.account}{p.kind === "pick" ? ` · ${t("review.pickInInvest")}` : ""}</span></span>
          </label>
        ))}
      </section>}
      {result.fixed.length > 0 && <section className="review-section">
        <h4 className="section-label">{t("review.fixed")}</h4>
        {result.fixed.map((f, i) => (
          <label key={i} className="review-row">
            <input type="checkbox" checked={!skip.has(`f${i}`)} onChange={() => toggle(`f${i}`)} />
            <CategoryTile category={f.category} size={30} />
            <span className="row-text"><span className="row-title">{f.name}</span>
              <span className="row-detail">{fmt.money(f.amount, f.currency)} · {t(`invest.freq.${f.frequency}` as never)} · {t("review.seen", { count: f.months })}</span></span>
          </label>
        ))}
      </section>}
      {result.categories.length > 0 && <section className="review-section">
        <h4 className="section-label">{t("review.categories")}</h4>
        {result.categories.map((c) => (
          <label key={c.tx_id} className="review-row">
            <input type="checkbox" checked={!skip.has(`c${c.tx_id}`)} onChange={() => toggle(`c${c.tx_id}`)} />
            <CategoryTile category={c.proposed} size={30} />
            <span className="row-text"><span className="row-title">{c.name}</span>
              <span className="row-detail">{fmt.date(c.day, { day: "numeric", month: "short" })} · {fmt.money(c.amount, c.currency)} · {categoryLabel(t, c.current || "none")} → <strong>{categoryLabel(t, c.proposed)}</strong> · {t(`review.reason.${c.reason}` as never)}</span></span>
          </label>
        ))}
      </section>}
      {error && <div className="error">{error}</div>}
      <div className="row between">
        <button className="btn ghost" onClick={() => setResult(null)}>{t("common.back")}</button>
        {total > 0 && <button className="btn" onClick={apply} disabled={busy || picked === 0}>{busy ? <Spinner /> : null} {t("review.apply", { count: picked })}</button>}
      </div>
    </div>
  );
}
