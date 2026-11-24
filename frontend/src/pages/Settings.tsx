import { motion } from "framer-motion";
import { useCallback, useEffect, useState } from "react";
import { api, type Connection, type Status, type Token } from "../api";
import { Glass } from "../glass/Glass";
import { rise, stagger } from "../ui/bits";

export function Settings({ status, notify, onLogout }: { status: Status; notify: (t: string) => void; onLogout: () => void }) {
  const [tokens, setTokens] = useState<Token[]>([]);
  const [scopes, setScopes] = useState<Record<string, string>>({});
  const [connections, setConnections] = useState<Connection[]>([]);
  const [banks, setBanks] = useState<{ name: string }[]>([]);
  const [bankQuery, setBankQuery] = useState("");
  const [name, setName] = useState("Jarvis");
  const [chosen, setChosen] = useState<string[]>([]);
  const [secret, setSecret] = useState("");

  const load = useCallback(() => {
    api.tokens().then(setTokens).catch(() => undefined);
    api.scopes().then((s) => { setScopes(s); setChosen((c) => (c.length ? c : Object.keys(s))); }).catch(() => undefined);
    if (status.banking) api.connections().then(setConnections).catch(() => undefined);
  }, [status.banking]);
  useEffect(load, [load]);
  useEffect(() => {
    const result = new URLSearchParams(location.search).get("bank");
    if (result) notify(result === "ok" ? "Banca collegata: sto scaricando i movimenti" : "Collegamento non riuscito");
  }, [notify]);

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <motion.div variants={rise} className="header"><h1 className="title">Altro</h1>
        <button className="btn ghost" onClick={async () => { await api.logout(); onLogout(); }}>Esci</button></motion.div>

      <motion.div variants={rise}>
        <Glass className="card" style={{ display: "grid", gap: 12 }}>
          <h3>Banche collegate</h3>
          {!status.banking ? (
            <div className="muted">Per collegare i conti serve Enable Banking (gratis per i propri conti): imposta <code>OINK_ENABLE_BANKING_APP_ID</code> e la chiave. Le istruzioni sono nel README.</div>
          ) : (
            <>
              {connections.map((c) => (
                <div key={c.id} className="row between">
                  <span>🏦 {c.institution}</span>
                  <span className={c.status === "active" ? "up" : "down"} style={{ fontSize: 13 }}>
                    {c.status === "active" ? `attiva${c.valid_until ? ` fino al ${new Date(c.valid_until).toLocaleDateString("it-IT")}` : ""}` : c.error || c.status}
                  </span>
                </div>
              ))}
              <div className="row">
                <input className="field" placeholder="Cerca la tua banca" value={bankQuery}
                  onFocus={() => banks.length || api.banks().then(setBanks).catch((e) => notify(e.message))}
                  onChange={(e) => setBankQuery(e.target.value)} />
                <button className="btn ghost" onClick={async () => { const r = await api.sync(); notify(`${r.added} movimenti nuovi`); load(); }}>Aggiorna</button>
              </div>
              {bankQuery && (
                <div className="row wrap" style={{ gap: 6 }}>
                  {banks.filter((b) => b.name.toLowerCase().includes(bankQuery.toLowerCase())).slice(0, 12).map((b) => (
                    <button key={b.name} className="chip" onClick={async () => { const { url } = await api.connect(b.name); location.href = url; }}>{b.name}</button>
                  ))}
                </div>
              )}
            </>
          )}
        </Glass>
      </motion.div>

      <motion.div variants={rise}>
        <Glass className="card" style={{ display: "grid", gap: 12 }}>
          <h3>App collegate</h3>
          <div className="muted" style={{ fontSize: 14 }}>Un token per ogni app (per esempio Jarvis), con i soli permessi che le servono. Lo vedi una volta sola.</div>
          {tokens.filter((t) => !t.revoked).map((t) => (
            <div key={t.id} className="row between wrap">
              <span><b>{t.name}</b> <span className="faint num">{t.prefix}…</span><div className="faint" style={{ fontSize: 12 }}>{t.scopes.length} permessi · {t.last_used ? `usato ${new Date(t.last_used).toLocaleDateString("it-IT")}` : "mai usato"}</div></span>
              <button className="chip" onClick={async () => { await api.revokeToken(t.id); load(); notify("Token revocato"); }}>Revoca</button>
            </div>
          ))}
          <input className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome dell'app" />
          <div className="scopes">
            {Object.entries(scopes).map(([key, label]) => (
              <label key={key}>
                <input type="checkbox" checked={chosen.includes(key)} onChange={(e) => setChosen((c) => (e.target.checked ? [...c, key] : c.filter((x) => x !== key)))} />
                <span>{label}<div className="faint" style={{ fontSize: 12 }}>{key}</div></span>
              </label>
            ))}
          </div>
          <button className="btn" disabled={!name || !chosen.length} onClick={async () => {
            const t = await api.newToken(name, chosen);
            setSecret(t.token);
            load();
          }}>Crea token</button>
          {secret && (
            <div style={{ display: "grid", gap: 8 }}>
              <div className="secret">{secret}</div>
              <button className="btn ghost" onClick={() => { navigator.clipboard.writeText(secret); notify("Copiato"); }}>Copia</button>
            </div>
          )}
        </Glass>
      </motion.div>

      <motion.div variants={rise}>
        <Glass className="card">
          <h3>Stato</h3>
          <div className="list">
            <div className="item"><span style={{ flex: 1 }}>IA per resoconti e consigli</span><span className={status.ai ? "up" : "faint"}>{status.ai ? "attiva" : "spenta"}</span></div>
            <div className="item"><span style={{ flex: 1 }}>Collegamento bancario</span><span className={status.banking ? "up" : "faint"}>{status.banking ? "pronto" : "non configurato"}</span></div>
            <div className="item"><span style={{ flex: 1 }}>Documentazione API</span><a href="/api/docs" className="faint">/api/docs →</a></div>
          </div>
        </Glass>
      </motion.div>
    </motion.div>
  );
}
