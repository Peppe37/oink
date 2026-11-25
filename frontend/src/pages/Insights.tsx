/* AI reports and questions. The numbers are Oink's; the AI only explains them. */

import { motion } from "framer-motion";
import { useEffect, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import { api, type Picture, type Report } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { Empty, PageHeader, Spinner, rise, stagger } from "../ui/bits";
import { Icon } from "../ui/icons";
import { AdviceCard, FixedCard, PictureCard, RiskCard } from "./Plan";

export function Insights({ ai, notify }: { ai: boolean; notify: (s: string) => void }) {
  const { t, fmt, lang } = useI18n();
  const errorText = useErrorText();
  const [reports, setReports] = useState<Report[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [asking, setAsking] = useState(false);

  const [picture, setPicture] = useState<Picture | null>(null);
  const loadPicture = () => { api.picture().then(setPicture).catch(() => undefined); };
  useEffect(() => { api.reports().then((r) => setReports(r.filter((x) => x.kind !== "advice"))).catch((e) => notify(errorText(e))); loadPicture(); }, [notify, errorText]);

  const generate = async () => {
    setBusy(true);
    try {
      const report = await api.generate(lang);
      setReports((old) => [report, ...(old ?? [])]);
    } catch (e) {
      notify(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const ask = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    setAnswer("");
    try {
      setAnswer((await api.ask(question, lang)).answer);
    } catch (err) {
      setAnswer(errorText(err));
    } finally {
      setAsking(false);
    }
  };
  const [latest, ...past] = reports ?? [];

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("reports.title")} action={ai ? <button className="btn" onClick={generate} disabled={busy}>{busy ? <><Spinner /> {t("reports.generating")}</> : <><Icon name="sparkle" size={18} /> <span className="hide-xs">{t("reports.generate")}</span></>}</button> : undefined} />

      <motion.div variants={rise}><PictureCard picture={picture} /></motion.div>
      <motion.div variants={rise}><AdviceCard ai={ai} notify={notify} /></motion.div>
      <div className="grid">
        <motion.div variants={rise} className="span-7"><FixedCard notify={notify} /></motion.div>
        <motion.div variants={rise} className="span-5"><RiskCard onSaved={loadPicture} /></motion.div>
      </div>

      {!ai && <motion.div variants={rise}><Glass className="banner" variant="tight"><span className="row" style={{ gap: 10 }}><Icon name="sparkle" /> {t("reports.aiOff")}</span></Glass></motion.div>}

      {ai && (
        <motion.div variants={rise}>
          <Glass refract strength={40} className="card">
            <h3>{t("reports.ask")}</h3>
            <form onSubmit={ask} className="ask">
              <input className="field" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={t("reports.askPlaceholder")} maxLength={1000} />
              <button className="btn" disabled={asking}>{asking ? <Spinner /> : t("reports.askButton")}</button>
            </form>
            {(asking || answer) && (
              <motion.div className="md answer" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
                {asking ? <span className="muted">{t("reports.thinking")}</span> : <ReactMarkdown>{answer}</ReactMarkdown>}
              </motion.div>
            )}
          </Glass>
        </motion.div>
      )}

      {reports === null ? <div className="skeleton" style={{ height: 300 }} /> : !latest ? (
        <Glass className="card"><Empty icon="insights" title={t("reports.empty")} /></Glass>
      ) : (
        <>
          <motion.div variants={rise}>
            <Glass className="card">
              <div className="row between"><h3 className="cap">{fmt.month(latest.month)}</h3><span className="faint small-text">{latest.model}</span></div>
              <div className="md"><ReactMarkdown>{latest.content}</ReactMarkdown></div>
            </Glass>
          </motion.div>
          {past.length > 0 && (
            <motion.div variants={rise} className="stack">
              <h4 className="day-head">{t("reports.archive")}</h4>
              {past.map((r) => (
                <details key={r.id} className="glass report-past">
                  <summary className="cap">{fmt.month(r.month)} <span className="faint small-text">{fmt.date(r.created)}</span></summary>
                  <div className="md"><ReactMarkdown>{r.content}</ReactMarkdown></div>
                </details>
              ))}
            </motion.div>
          )}
        </>
      )}
    </motion.div>
  );
}
