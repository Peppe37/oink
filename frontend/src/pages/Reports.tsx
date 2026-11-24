import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { api, monthName, type Report } from "../api";
import { Glass } from "../glass/Glass";
import { rise, stagger } from "../ui/bits";

const SUGGESTIONS = ["Quanto ho speso in ristoranti questo mese?", "Su cosa posso risparmiare?", "Gli abbonamenti mi costano troppo?"];

export function Reports({ ai, notify }: { ai: boolean; notify: (t: string) => void }) {
  const [reports, setReports] = useState<Report[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<{ q: string; a: string } | null>(null);
  const [thinking, setThinking] = useState(false);

  useEffect(() => { api.reports().then(setReports).catch((e) => notify(e.message)); }, [notify]);

  const ask = async (q: string) => {
    if (!q.trim()) return;
    setThinking(true);
    setAnswer(null);
    try { setAnswer({ q, a: (await api.ask(q)).answer }); setQuestion(""); }
    catch (e) { notify(e instanceof Error ? e.message : "Errore"); }
    finally { setThinking(false); }
  };

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <motion.div variants={rise} className="header">
        <h1 className="title">Resoconti</h1>
        <button className="btn" disabled={!ai || busy} onClick={async () => {
          setBusy(true);
          try { const r = await api.generate(); setReports((old) => [r, ...(old ?? [])]); }
          catch (e) { notify(e instanceof Error ? e.message : "Errore"); }
          finally { setBusy(false); }
        }}>{busy ? "Ci penso…" : "✨ Resoconto del mese"}</button>
      </motion.div>

      {!ai && <motion.div variants={rise}><Glass className="card muted">L'IA è spenta: imposta <code>OINK_AI_MODEL</code> (qualsiasi provider) per resoconti e consigli. Statistiche e grafici funzionano lo stesso.</Glass></motion.div>}

      {ai && (
        <motion.div variants={rise}>
          <Glass refract strength={40} className="card" style={{ display: "grid", gap: 12 }}>
            <h3>Chiedi al tuo salvadanaio</h3>
            <form className="row" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
              <input className="field" placeholder="Fai una domanda sulle tue finanze" value={question} onChange={(e) => setQuestion(e.target.value)} />
              <button className="btn" disabled={thinking || !question.trim()}>Chiedi</button>
            </form>
            <div className="row wrap" style={{ gap: 6 }}>{SUGGESTIONS.map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}</div>
            <AnimatePresence mode="wait">
              {thinking && <motion.div key="t" className="muted" initial={{ opacity: 0 }} animate={{ opacity: [0.4, 1, 0.4] }} exit={{ opacity: 0 }} transition={{ repeat: Infinity, duration: 1.4 }}>Sto guardando i conti…</motion.div>}
              {answer && <motion.div key="a" className="md" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}><div className="faint">{answer.q}</div><ReactMarkdown>{answer.a}</ReactMarkdown></motion.div>}
            </AnimatePresence>
          </Glass>
        </motion.div>
      )}

      {reports?.map((r) => (
        <motion.div key={r.id} variants={rise}>
          <Glass className="card">
            <div className="row between"><h3 style={{ textTransform: "capitalize" }}>{monthName(r.month)}</h3><span className="faint" style={{ fontSize: 12 }}>{r.model}</span></div>
            <div className="md"><ReactMarkdown>{r.content}</ReactMarkdown></div>
          </Glass>
        </motion.div>
      ))}
      {reports?.length === 0 && ai && <Glass className="card faint">Il primo resoconto arriva a inizio mese, oppure generalo ora.</Glass>}
    </motion.div>
  );
}
