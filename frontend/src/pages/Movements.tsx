import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { api, catStyle, eur, type Account, type Tx } from "../api";
import { Glass } from "../glass/Glass";
import { rise, stagger } from "../ui/bits";

export function Movements({ notify }: { notify: (t: string) => void }) {
  const [rows, setRows] = useState<Tx[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [month, setMonth] = useState("");
  const [category, setCategory] = useState<string | undefined>();
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    api.accounts().then(setAccounts).catch(() => undefined);
    api.categories().then(setCategories).catch(() => undefined);
  }, []);
  useEffect(() => {
    const t = setTimeout(() => {
      api.transactions({ q, month, category, limit: 300 }).then(setRows).catch((e) => notify(e.message));
    }, 220);
    return () => clearTimeout(t);
  }, [q, month, category, notify]);

  const byDay = useMemo(() => {
    const groups = new Map<string, Tx[]>();
    for (const tx of rows ?? []) groups.set(tx.day, [...(groups.get(tx.day) ?? []), tx]);
    return [...groups.entries()];
  }, [rows]);
  const name = (id: number) => accounts.find((a) => a.id === id)?.name ?? "";

  const setCat = async (tx: Tx, cat: string) => {
    const updated = await api.patchTx(tx.id, { category: cat });
    setRows((old) => old?.map((r) => (r.id === tx.id ? updated : r)) ?? null);
    setOpen(null);
    notify(`«${cat}» d'ora in poi anche per ${tx.counterparty || tx.description.slice(0, 24)}`);
  };

  const uncategorised = rows?.filter((r) => !r.category).length ?? 0;

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <motion.div variants={rise} className="header"><h1 className="title">Movimenti</h1></motion.div>
      <motion.div variants={rise}>
        <Glass className="card" style={{ display: "grid", gap: 12 }}>
          <input className="field" placeholder="Cerca: Esselunga, affitto, Netflix…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="row wrap" style={{ gap: 8 }}>
            <input className="field" type="month" value={month} onChange={(e) => setMonth(e.target.value)} style={{ width: "auto", padding: "8px 12px" }} />
            <button className={`chip ${category === undefined ? "on" : ""}`} onClick={() => setCategory(undefined)}>Tutte</button>
            <button className={`chip ${category === "" ? "on" : ""}`} onClick={() => setCategory("")}>Da classificare</button>
            {categories.slice(0, 10).map((c) => (
              <button key={c} className={`chip ${category === c ? "on" : ""}`} onClick={() => setCategory(c)}>{catStyle(c).emoji} {c}</button>
            ))}
          </div>
          {uncategorised > 0 && category !== "" && (
            <div className="row between">
              <span className="muted">{uncategorised} movimenti senza categoria</span>
              <button className="btn ghost" onClick={async () => {
                try { const r = await api.categorize(); notify(`L'IA ne ha classificati ${r.categorized}`); setQ((x) => `${x}`); setRows(await api.transactions({ q, month, category, limit: 300 })); }
                catch (e) { notify(e instanceof Error ? e.message : "Errore"); }
              }}>✨ Classifica con l'IA</button>
            </div>
          )}
        </Glass>
      </motion.div>

      {rows === null ? <div className="skeleton" style={{ height: 300, borderRadius: 28 }} /> : byDay.length === 0 ? (
        <Glass className="card faint">Nessun movimento. Collega una banca o carica un estratto dai Conti.</Glass>
      ) : byDay.map(([day, txs]) => (
        <motion.div key={day} variants={rise}>
          <div className="muted" style={{ margin: "6px 8px", fontSize: 13, fontWeight: 600, textTransform: "capitalize" }}>
            {new Date(day).toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" })}
          </div>
          <Glass className="card" style={{ padding: "6px 18px" }}>
            <div className="list">
              {txs.map((tx) => (
                <div key={tx.id}>
                  <div className="item" onClick={() => setOpen(open === tx.id ? null : tx.id)} style={{ cursor: "pointer" }}>
                    <div className="dot" style={{ background: `${catStyle(tx.category).color}26` }}>{tx.category ? catStyle(tx.category).emoji : "❓"}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="ellipsis" style={{ fontWeight: 500 }}>{tx.counterparty || tx.description}</div>
                      <div className="faint ellipsis" style={{ fontSize: 13 }}>{tx.category || "da classificare"} · {name(tx.account_id)}</div>
                    </div>
                    <div className={`num ${tx.amount_cents > 0 ? "up" : ""}`} style={{ fontWeight: 600 }}>{eur(tx.amount_cents, { sign: tx.amount_cents > 0 })}</div>
                  </div>
                  <AnimatePresence>
                    {open === tx.id && (
                      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} style={{ overflow: "hidden" }}>
                        <div className="faint" style={{ fontSize: 13, padding: "0 4px 10px 50px" }}>{tx.description}</div>
                        <div className="row wrap" style={{ gap: 6, padding: "0 4px 14px 50px" }}>
                          {categories.map((c) => (
                            <button key={c} className={`chip ${tx.category === c ? "on" : ""}`} onClick={() => setCat(tx, c)}>{catStyle(c).emoji} {c}</button>
                          ))}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              ))}
            </div>
          </Glass>
        </motion.div>
      ))}
    </motion.div>
  );
}
