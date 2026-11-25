/* Every movement, grouped by day. Tap one to sort it into a category (Oink learns the
   merchant) or add a note; add cash movements by hand. */

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Account, type PlanItem, type Tx } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { Empty, Field, PageHeader, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { CategoryTile, Icon } from "../ui/icons";
import { CATEGORIES, categoryLabel } from "../ui/labels";

const PAGE = 80;

export function Activity({ ai, notify }: { ai: boolean; notify: (s: string) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [items, setItems] = useState<Tx[] | null>(null);
  const [more, setMore] = useState(false);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [q, setQ] = useState("");
  const [account, setAccount] = useState<number | undefined>();
  const [selected, setSelected] = useState<Tx | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (offset = 0) => {
    const page = await api.transactions({ q, account_id: account, limit: PAGE, offset });
    setItems((old) => (offset && old ? [...old, ...page] : page));
    setMore(page.length === PAGE);
  }, [q, account]);

  useEffect(() => {
    const id = setTimeout(() => load().catch((e) => notify(errorText(e))), q ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, q, notify, errorText]);
  useEffect(() => { api.accounts().then(setAccounts).catch(() => undefined); }, []);

  const byDay = useMemo(() => {
    const groups: [string, Tx[]][] = [];
    for (const tx of items ?? []) {
      const last = groups[groups.length - 1];
      if (last && last[0] === tx.day) last[1].push(tx);
      else groups.push([tx.day, [tx]]);
    }
    return groups;
  }, [items]);

  const dayLabel = (day: string) => {
    const today = new Date().toISOString().slice(0, 10);
    const yesterday = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
    if (day === today) return t("movements.today");
    if (day === yesterday) return t("movements.yesterday");
    return fmt.date(day, { weekday: "long", day: "numeric", month: "long" });
  };

  const categorize = async () => {
    setBusy(true);
    try {
      const { categorized } = await api.categorize();
      notify(t("movements.categorized", { count: categorized }));
      await load();
    } catch (e) {
      notify(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const accountName = (id: number) => accounts.find((a) => a.id === id)?.name ?? "";

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("movements.title")} action={
        <div className="row" style={{ gap: 8 }}>
          {ai && <button className="icon-btn glassy" onClick={categorize} disabled={busy} aria-label={t("movements.categorize")} title={t("movements.categorize")}>{busy ? <Spinner /> : <Icon name="sparkle" />}</button>}
          <button className="icon-btn glassy" onClick={() => setAdding(true)} aria-label={t("movements.add")} title={t("movements.add")}><Icon name="plus" /></button>
        </div>
      } />

      <motion.div variants={rise} className="toolbar">
        <Glass variant="pill" className="search">
          <Icon name="search" size={18} className="faint" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("movements.search")} aria-label={t("common.search")} />
        </Glass>
        {accounts.length > 1 && (
          <div className="chips">
            <button className={`chip ${account === undefined ? "on" : ""}`} onClick={() => setAccount(undefined)}>{t("common.all")}</button>
            {accounts.map((a) => <button key={a.id} className={`chip ${account === a.id ? "on" : ""}`} onClick={() => setAccount(a.id)}>{a.name}</button>)}
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
              <h4 className="day-head cap">{dayLabel(day)}</h4>
              <Glass className="card list tight-card">
                {txs.map((tx) => (
                  <button key={tx.id} className="item press-row" onClick={() => setSelected(tx)}>
                    <CategoryTile category={tx.category} />
                    <span className="item-main">
                      <span className="row-text">
                        <span className="row-title ellipsis">{tx.counterparty || tx.description}</span>
                        <span className="row-detail ellipsis">{tx.source === "pending" ? `${t("movements.pending")} · ` : ""}{categoryLabel(t, tx.category)}{accounts.length > 1 ? ` · ${accountName(tx.account_id)}` : ""}</span>
                      </span>
                      <span className={`num amount ${Number(tx.amount) > 0 ? "up" : ""}`}>{fmt.money(tx.amount, tx.currency, { sign: Number(tx.amount) > 0 })}</span>
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
  const [error, setError] = useState("");
  useEffect(() => {
    if (!tx) return;
    setCategory(tx.category);
    setCustom(tx.category && !CATEGORIES.includes(tx.category) ? tx.category : "");
    setNote(tx.note);
    setError("");
  }, [tx]);
  if (!tx) return <Sheet open={false} onClose={onClose}>{null}</Sheet>;

  const save = async () => {
    try {
      onSaved(await api.patchTx(tx.id, { category: custom.trim() || category, note, learn }));
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
      {Number(tx.amount) < 0 && <MarkAs tx={tx} onDone={onSaved} />}
      <h4 className="section-label">{t("movements.category")}</h4>
      <div className="cat-grid">
        {CATEGORIES.map((c) => (
          <button key={c} className={`cat ${category === c && !custom ? "on" : ""}`} onClick={() => { setCategory(c); setCustom(""); }}>
            <CategoryTile category={c} size={44} />
            <span>{categoryLabel(t, c)}</span>
          </button>
        ))}
      </div>
      <div className="stack">
        <input className="field" placeholder={t("movements.customCategory")} value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={40} />
        <label className="check"><input type="checkbox" checked={learn} onChange={(e) => setLearn(e.target.checked)} /> {t("movements.learn")}</label>
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
