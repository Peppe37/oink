import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, eur, monthName, type Account, type StatementRequest } from "../api";
import { Glass } from "../glass/Glass";
import { Money, rise, stagger } from "../ui/bits";

const GRADIENTS = [
  "linear-gradient(135deg,#ff6fb1,#8b7bff)", "linear-gradient(135deg,#ffb38a,#ff6fb1)",
  "linear-gradient(135deg,#5ee6c4,#4f8cff)", "linear-gradient(135deg,#8b7bff,#2b2140)",
  "linear-gradient(135deg,#ffd166,#ff8a5b)", "linear-gradient(135deg,#4fd1ff,#5ee6c4)",
];
const KIND: Record<string, string> = { checking: "Conto corrente", savings: "Risparmio", card: "Carta", cash: "Contanti", investment: "Investimenti" };

export function Accounts({ notify }: { notify: (t: string) => void }) {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [requests, setRequests] = useState<StatementRequest[]>([]);
  const [adding, setAdding] = useState(false);
  const [target, setTarget] = useState<number | null>(null);

  const load = useCallback(() => {
    api.accounts().then(setAccounts).catch((e) => notify(e.message));
    api.requests().then(setRequests).catch(() => undefined);
  }, [notify]);
  useEffect(load, [load]);

  const total = accounts?.reduce((s, a) => s + (a.balance_cents ?? 0), 0) ?? 0;

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <motion.div variants={rise} className="header">
        <div><h1 className="title">Conti</h1><div className="muted">In totale <Money cents={total} /></div></div>
        <button className="btn" onClick={() => setAdding(true)}>+ Conto</button>
      </motion.div>

      {requests.length > 0 && (
        <motion.div variants={rise}>
          <Glass className="card">
            <h3>Estratti da caricare</h3>
            <div className="list">
              {requests.map((r) => {
                const acc = accounts?.find((a) => a.id === r.account_id);
                return (
                  <div key={r.id} className="item">
                    <div className="dot" style={{ background: "color-mix(in srgb, var(--pink) 20%, transparent)" }}>📬</div>
                    <div style={{ flex: 1 }}>{acc?.name}<div className="faint" style={{ fontSize: 13, textTransform: "capitalize" }}>{monthName(r.month)}</div></div>
                    <button className="btn" style={{ padding: "8px 14px" }} onClick={() => setTarget(r.account_id)}>Carica</button>
                    <button className="chip" onClick={async () => { await api.skipRequest(r.id); load(); }}>Salta</button>
                  </div>
                );
              })}
            </div>
          </Glass>
        </motion.div>
      )}

      <div className="grid">
        {accounts?.map((acc, i) => (
          <motion.div key={acc.id} variants={rise} className="span-4" whileHover={{ y: -6, rotateX: 4 }} style={{ perspective: 800 }}>
            <Glass refract={i < 3} strength={30} press className="wallet-card" onClick={() => setTarget(acc.id)}
              style={{ background: `${acc.color || GRADIENTS[i % GRADIENTS.length]}`, boxShadow: "0 24px 50px -18px rgba(0,0,0,.6), inset 0 1px 0 rgba(255,255,255,.5)" }}>
              <div className="row between">
                <span style={{ fontWeight: 700 }}>{acc.name}</span>
                <span style={{ opacity: 0.8, fontSize: 12 }}>{acc.source === "bank" ? "🔗 collegato" : "manuale"}</span>
              </div>
              <div>
                <div style={{ opacity: 0.8, fontSize: 13 }}>{acc.institution || KIND[acc.kind]}</div>
                <div style={{ fontSize: 28, fontWeight: 800, letterSpacing: "-0.03em" }} className="num">{eur(acc.balance_cents)}</div>
              </div>
            </Glass>
          </motion.div>
        ))}
        {accounts?.length === 0 && (
          <motion.div variants={rise} className="span-12">
            <Glass className="card muted">Aggiungi un conto, oppure collega la banca da «Altro».</Glass>
          </motion.div>
        )}
      </div>

      <AnimatePresence>
        {adding && <AddAccount onClose={() => setAdding(false)} onSaved={() => { setAdding(false); load(); notify("Conto aggiunto"); }} />}
        {target !== null && accounts && (
          <Upload account={accounts.find((a) => a.id === target)!} onClose={() => setTarget(null)}
            onDone={(text) => { setTarget(null); load(); notify(text); }} />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Sheet({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,0,0,.35)", display: "grid", placeItems: "end center", padding: 16 }}>
      <Glass refract strength={46} className="card" onClick={(e) => e.stopPropagation()}
        initial={{ y: 80, scale: 0.96 }} animate={{ y: 0, scale: 1 }} exit={{ y: 80, opacity: 0 }} transition={{ type: "spring", stiffness: 280, damping: 28 }}
        style={{ width: "min(520px, 100%)", marginBottom: "max(90px, env(safe-area-inset-bottom))", display: "grid", gap: 14 }}>
        {children}
      </Glass>
    </motion.div>
  );
}

function AddAccount({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("checking");
  const [institution, setInstitution] = useState("");
  const [balance, setBalance] = useState("");
  return (
    <Sheet onClose={onClose}>
      <h2 style={{ margin: 0 }}>Nuovo conto</h2>
      <input className="field" placeholder="Nome (es. Carta Hype)" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      <div className="row wrap" style={{ gap: 6 }}>
        {Object.entries(KIND).map(([k, label]) => <button key={k} className={`chip ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>{label}</button>)}
      </div>
      <input className="field" placeholder="Banca o istituto (facoltativo)" value={institution} onChange={(e) => setInstitution(e.target.value)} />
      <input className="field" inputMode="decimal" placeholder="Saldo attuale in € (facoltativo)" value={balance} onChange={(e) => setBalance(e.target.value)} />
      <div className="faint" style={{ fontSize: 13 }}>Ogni inizio mese Oink ti chiederà l'estratto conto di questo conto.</div>
      <button className="btn" disabled={!name} onClick={async () => {
        const cents = balance ? Math.round(parseFloat(balance.replace(/\./g, "").replace(",", ".")) * 100) : null;
        await api.addAccount({ name, kind, institution, balance_cents: Number.isFinite(cents) ? cents : null });
        onSaved();
      }}>Aggiungi</button>
    </Sheet>
  );
}

function Upload({ account, onClose, onDone }: { account: Account; onClose: () => void; onDone: (text: string) => void }) {
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const send = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.importFile(account.id, file);
      onDone(`${r.added} movimenti nuovi${r.duplicates ? `, ${r.duplicates} già presenti` : ""}${r.by_ai ? " (letti dall'IA)" : ""}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Errore");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Sheet onClose={onClose}>
      <h2 style={{ margin: 0 }}>{account.name}</h2>
      <div className={`drop ${over ? "over" : ""}`} onClick={() => input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files[0]); }}>
        {busy ? (
          <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1.2, ease: "linear" }} style={{ display: "inline-block", fontSize: 28 }}>🐷</motion.div>
        ) : (<><div style={{ fontSize: 30 }}>📄</div><div style={{ fontWeight: 600 }}>Trascina qui l'estratto conto</div><div className="faint">CSV della banca o PDF</div></>)}
        <input ref={input} type="file" accept=".csv,.pdf,.txt,.tsv" hidden onChange={(e) => send(e.target.files?.[0])} />
      </div>
      {error && <div className="down">{error}</div>}
    </Sheet>
  );
}
