/* AI reports and questions. The numbers are Oink's; the AI only explains them. */

import { motion } from "framer-motion";
import { useEffect, useState, type FormEvent } from "react";
import { Markdown as ReactMarkdown } from "../ui/Markdown";
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

  const [month, setMonth] = useState(new Date().toISOString().slice(0, 7));
  const [writing, setWriting] = useState<string | null>(null); // the month being written
  const [months, setMonths] = useState<{ month: string; report: boolean }[]>([]);
  const loadMonths = () => { api.reportMonths().then(setMonths).catch(() => undefined); };
  useEffect(loadMonths, []);
  const generate = async (forMonth?: string) => {
    const target = forMonth ?? month;
    setBusy(true);
    setWriting(target);
    try {
      const report = await api.generate(lang, target);
      setReports((old) => [report, ...(old ?? []).filter((r) => r.month !== report.month)].sort((a, b) => b.month.localeCompare(a.month)));
      loadMonths();
    } catch (e) {
      notify(errorText(e));
    } finally {
      setBusy(false);
      setWriting(null);
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

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("reports.title")} action={ai ? <span className="row" style={{ gap: 8 }}>
        <input className="field month-pick" type="month" value={month} max={new Date().toISOString().slice(0, 7)} onChange={(e) => setMonth(e.target.value)} aria-label={t("reports.month")} />
        <button className="btn" onClick={() => generate()} disabled={busy}>{busy ? <><Spinner /> {t("reports.generating")}</> : <><Icon name="sparkle" size={18} /> <span className="hide-xs">{t("reports.generate")}</span></>}</button></span> : undefined} />

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

      {reports === null ? <div className="skeleton" style={{ height: 300 }} /> : (
        <motion.div variants={rise} className="stack">
          <h4 className="day-head">{t("reports.list")}</h4>
          {writing && !(reports ?? []).some((r) => r.month === writing) && <Writing month={writing} />}
          {(reports ?? []).length === 0 && !writing && <Glass className="card"><Empty icon="insights" title={t("reports.empty")} /></Glass>}
          {(reports ?? []).map((r) => writing === r.month ? <Writing key={r.id} month={r.month} /> : (
            <details key={r.id} className="glass report-past">
              <summary className="cap"><span>{fmt.month(r.month)} <span className="faint small-text">· {t("reports.writtenOn", { date: fmt.date(r.created) })}</span></span>
                {ai && <button className="btn ghost small" style={{ marginLeft: "auto" }} disabled={busy} onClick={(e) => { e.preventDefault(); generate(r.month); }}><Icon name="sync" size={14} /> {t("reports.regenerate")}</button>}</summary>
              <div className="md"><ReactMarkdown>{r.content}</ReactMarkdown></div>
            </details>
          ))}
          {ai && months.some((m) => !m.report && m.month !== writing) && (
            <Glass className="card" variant="tight">
              <h4 className="section-label">{t("reports.missing")}</h4>
              <div className="row wrap" style={{ gap: 8 }}>
                {months.filter((m) => !m.report && m.month !== writing).map((m) => (
                  <button key={m.month} className="btn ghost small cap" disabled={busy} onClick={() => generate(m.month)}><Icon name="sparkle" size={14} /> {fmt.month(m.month)}</button>
                ))}
              </div>
            </Glass>
          )}
        </motion.div>
      )}
    </motion.div>
  );
}

/* While a report is written its old text is hidden: a placeholder says which month. */
function Writing({ month }: { month: string }) {
  const { t, fmt } = useI18n();
  return (
    <Glass className="report-past writing" aria-busy="true">
      <div className="row" style={{ gap: 10 }}><Spinner /> <strong>{t("reports.writing", { month: fmt.month(month) })}</strong></div>
      <p className="faint small-text" style={{ margin: "6px 0 10px" }}>{t("reports.writingHint", { month: fmt.month(month) })}</p>
      <div className="skeleton" style={{ height: 14, width: "92%", marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 14, width: "78%", marginBottom: 8 }} />
      <div className="skeleton" style={{ height: 14, width: "85%" }} />
    </Glass>
  );
}
