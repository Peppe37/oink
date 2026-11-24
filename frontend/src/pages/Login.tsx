import { motion } from "framer-motion";
import { useState } from "react";
import { api } from "../api";
import { Glass } from "../glass/Glass";
import { Pig } from "../ui/bits";

export function Login({ firstRun, onDone }: { firstRun: boolean; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await (firstRun ? api.setup(password) : api.login(password));
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Errore");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="center">
      <Glass refract strength={52} className="card" style={{ width: "min(420px, 100%)", padding: 32 }}
        initial={{ opacity: 0, y: 30, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ type: "spring", stiffness: 200, damping: 22 }}>
        <form onSubmit={submit} style={{ display: "grid", gap: 18 }}>
          <div className="row" style={{ gap: 14 }}>
            <Pig size={52} />
            <div>
              <h1 className="title" style={{ fontSize: 30 }}>Oink</h1>
              <div className="muted">{firstRun ? "Scegli la password del tuo salvadanaio" : "Bentornato"}</div>
            </div>
          </div>
          <input className="field" type="password" autoFocus autoComplete={firstRun ? "new-password" : "current-password"}
            placeholder={firstRun ? "Almeno 10 caratteri" : "Password"} value={password} onChange={(e) => setPassword(e.target.value)} />
          {error && <motion.div initial={{ x: -8 }} animate={{ x: [8, -6, 4, 0] }} className="down">{error}</motion.div>}
          <button className="btn" disabled={busy || password.length < (firstRun ? 10 : 1)}>{firstRun ? "Inizia" : "Entra"}</button>
        </form>
      </Glass>
    </div>
  );
}
