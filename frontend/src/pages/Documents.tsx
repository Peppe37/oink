/* The financial documents hub: drop statements, payslips, contracts, invoices; Oink reads
   and files them (statements also fill their account), finds anything in them, and answers
   questions citing the documents. */

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Markdown as ReactMarkdown } from "../ui/Markdown";
import { api, type Account, type Doc, type DocKind, type DocSource, type ProposedAccount } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n, type Key } from "../i18n";
import { Empty, Field, PageHeader, Row, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { Icon, Tile } from "../ui/icons";

const KINDS: DocKind[] = ["statement", "payslip", "contract", "invoice", "receipt", "tax", "insurance", "investment", "other"];
const LOOK: Record<DocKind, { icon: string; colors: [string, string] }> = {
  statement: { icon: "bank", colors: ["#ff8cc0", "#c94b8c"] },
  payslip: { icon: "salary", colors: ["#5ef0aa", "#16b06a"] },
  contract: { icon: "contract", colors: ["#9b8cff", "#5a45d6"] },
  invoice: { icon: "fees", colors: ["#ffd76a", "#f2a71b"] },
  receipt: { icon: "receipt", colors: ["#ffb26b", "#f0703c"] },
  tax: { icon: "taxes", colors: ["#c4c4d4", "#7f7f97"] },
  insurance: { icon: "shield", colors: ["#6cc9ff", "#2f86e8"] },
  investment: { icon: "investment", colors: ["#6ee7d0", "#13897e"] },
  other: { icon: "file", colors: ["#b3aec2", "#7c7690"] },
};
const kindKey = (k: DocKind) => `docs.kind.${k}` as Key;
type Message = { role: "user" | "assistant"; content: string; sources?: DocSource[]; note?: string };

export function Documents({ ai, notify }: { ai: boolean; notify: (s: string) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [kind, setKind] = useState<DocKind | "">("");
  const [onlyReview, setOnlyReview] = useState(false);
  const [target, setTargetAccount] = useState<number | "">("");
  const [toReview, setToReview] = useState(0);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Doc | null>(null);
  const [busy, setBusy] = useState("");
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    api.documents({ kind: kind || undefined, q: q.trim() || undefined, status: onlyReview ? "review" : undefined }).then(setDocs).catch((e) => notify(errorText(e)));
    api.documents({ status: "review" }).then((r) => setToReview(r.length)).catch(() => undefined);
  }, [kind, q, onlyReview, notify, errorText]);
  const reading = (docs ?? []).some((d) => d.status === "processing");
  useEffect(() => { if (!reading) return; const timer = setInterval(load, 3000); return () => clearInterval(timer); }, [reading, load]);
  useEffect(() => { const timer = setTimeout(load, q ? 250 : 0); return () => clearTimeout(timer); }, [load, q]);
  useEffect(() => { api.accounts().then(setAccounts).catch(() => undefined); }, []);
  const accountName = (id: number | null) => accounts.find((a) => a.id === id)?.name ?? "";

  const upload = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      setBusy(file.name);
      try {
        const r = await api.uploadDocument(file, { accountId: target || undefined });
        if (r.status === "processing") notify(t("docs.reading", { name: file.name }));
        else if (r.import_note === "account_not_recognised" && r.facts.proposed_account) { setOpen(r); notify(t("docs.newAccountFound")); }
        else if (r.duplicate) notify(t("docs.duplicate", { title: r.title }));
        else if (r.import?.added) notify(t("docs.imported", { title: r.title, count: r.import.added }));
        else notify(t("docs.uploaded", { title: r.title }));
      } catch (e) {
        notify(`${file.name}: ${errorText(e)}`);
      }
    }
    setBusy("");
    load();
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files.length) upload(e.dataTransfer.files); };

  const byYear = useMemo(() => {
    const groups = new Map<string, Doc[]>();
    for (const d of docs ?? []) {
      const year = (d.date ?? d.created ?? "").slice(0, 4) || "—";
      groups.set(year, [...(groups.get(year) ?? []), d]);
    }
    return [...groups.entries()];
  }, [docs]);

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show"
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
      <PageHeader title={t("docs.title")} subtitle={t("docs.subtitle")} action={
        <button className="btn" onClick={() => input.current?.click()} disabled={!!busy}>
          {busy ? <><Spinner /> <span className="hide-xs">{t("docs.uploading", { name: busy })}</span></> : <><Icon name="upload" size={18} /> {t("docs.upload")}</>}
        </button>} />
      <input ref={input} type="file" multiple hidden accept=".pdf,.csv,.tsv,.txt,.ofx,.qfx,.md,.json,.xml,.png,.jpg,.jpeg,.webp,image/*"
        onChange={(e) => { if (e.target.files?.length) upload(e.target.files); e.target.value = ""; }} />

      {(toReview > 0 || onlyReview) && (
        <motion.div variants={rise}>
          <Glass className="banner review-banner" variant="tight">
            <span className="row" style={{ gap: 10 }}><Icon name="shield" /> {onlyReview ? t("docs.reviewShowing") : t("docs.reviewCount", { count: toReview })}</span>
            <button className="btn ghost small" onClick={() => setOnlyReview((v) => !v)}>{onlyReview ? t("docs.all") : t("docs.reviewOpen")}</button>
          </Glass>
        </motion.div>
      )}

      <motion.div variants={rise}><DocChat ai={ai} onOpen={(id) => api.document(id).then(setOpen).catch((e) => notify(errorText(e)))} /></motion.div>

      <motion.div variants={rise} className="stack">
        <Glass variant="pill" className="search">
          <Icon name="search" size={18} className="faint" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("docs.search")} aria-label={t("docs.search")} />
        </Glass>
        <div className="chips scroll-x">
          <button className={`chip ${kind === "" ? "on" : ""}`} onClick={() => setKind("")}>{t("docs.all")}</button>
          {KINDS.map((k) => <button key={k} className={`chip ${kind === k ? "on" : ""}`} onClick={() => setKind(k)}>{t(kindKey(k))}</button>)}
        </div>
      </motion.div>

      <motion.div variants={rise}>
        <div className="row wrap upload-target" style={{ gap: 8, marginBottom: 10 }}>
          <span className="muted small-text">{t("docs.intoAccount")}</span>
          <select className="field" style={{ flex: "0 1 260px" }} value={target} onChange={(e) => setTargetAccount(e.target.value ? Number(e.target.value) : "")}>
            <option value="">{t("docs.accountAuto")}</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution && a.institution !== a.name ? ` (${a.institution})` : ""}</option>)}
          </select>
        </div>
        <div className={`drop ${over ? "over" : ""}`} onClick={() => input.current?.click()} role="button" tabIndex={0}>
          <Icon name="upload" size={22} />
          <strong>{t("docs.drop")}</strong>
          <span className="small-text">{t("docs.inbox")}</span>
        </div>
      </motion.div>

      {docs === null ? <div className="skeleton" style={{ height: 180 }} />
        : docs.length === 0 ? (
          <motion.div variants={rise}><Glass className="card"><Empty icon="documents" title={t("docs.empty")} hint={q || kind ? undefined : t("docs.emptyHint")} /></Glass></motion.div>
        ) : byYear.map(([year, items]) => (
          <motion.section variants={rise} key={year}>
            <h4 className="day-head">{year}</h4>
            <Glass className="card list tight-card">
              {items.map((d) => (
                <Row key={d.id} onClick={() => api.document(d.id).then(setOpen).catch((e) => notify(errorText(e)))} chevron
                  icon={<Tile icon={LOOK[d.kind].icon} from={LOOK[d.kind].colors[0]} to={LOOK[d.kind].colors[1]} size={38} />}
                  title={d.title}
                  right={d.status === "processing" ? <span className="badge soft"><Spinner /> {t("docs.status.processing")}</span>
                    : d.status === "review" ? <span className="badge warn">{t("docs.status.review")}</span>
                    : d.status === "error" ? <span className="badge warn">{t("docs.status.error")}</span> : undefined}
                  detail={[t(kindKey(d.kind)), d.issuer, d.date && fmt.date(d.date, { day: "numeric", month: "short", year: "numeric" }),
                    d.imported != null && d.account_id ? t("docs.importedInto", { count: d.imported, account: accountName(d.account_id) }) : "",
                    d.kind === "statement" && d.imported == null ? t("docs.notImported") : ""].filter(Boolean).join(" · ")} />
              ))}
            </Glass>
          </motion.section>
        ))}

      <DocSheet doc={open} accounts={accounts} onClose={() => setOpen(null)} notify={notify}
        onChanged={(d) => { setOpen(d); load(); }} onDeleted={() => { setOpen(null); load(); notify(t("docs.deleted")); }} />
    </motion.div>
  );
}

function DocChat({ ai, onOpen }: { ai: boolean; onOpen: (id: number) => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [messages, asking]);

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    const text = question.trim();
    if (!text || asking) return;
    const history = messages.map(({ role, content }) => ({ role, content }));
    setMessages((m) => [...m, { role: "user", content: text }]);
    setQuestion("");
    setAsking(true);
    try {
      const r = await api.askDocuments(text, history);
      const content = r.answer ?? (r.sources.length ? "" : t("docs.chatNothing"));
      setMessages((m) => [...m, { role: "assistant", content, sources: r.sources, note: !r.ai && r.sources.length ? t("docs.chatNoAI") : undefined }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: errorText(err) }]);
    } finally {
      setAsking(false);
    }
  };

  return (
    <Glass refract strength={26} className="card doc-chat">
      <div className="row between">
        <h3 className="row" style={{ gap: 8, margin: 0 }}><Icon name="chat" size={18} /> {t("docs.chatTitle")}</h3>
        {messages.length > 0 && <button className="btn ghost small" onClick={() => setMessages([])}>{t("docs.clear")}</button>}
      </div>
      {messages.length > 0 && (
        <div className="chat-log">
          <AnimatePresence initial={false}>
            {messages.map((m, i) => (
              <motion.div key={i} className={`bubble ${m.role}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
                {m.note && <p className="muted small-text">{m.note}</p>}
                {m.content && (m.role === "assistant" ? <div className="md"><ReactMarkdown>{m.content}</ReactMarkdown></div> : <p>{m.content}</p>)}
                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    <span className="faint small-text">{t("docs.sources")}</span>
                    {m.sources.slice(0, m.content ? 5 : 8).map((s) => (
                      <button key={s.n} className="source" onClick={() => onOpen(s.document_id)} title={s.snippet}>
                        <span className="source-n">{s.n}</span>
                        <span className="ellipsis">{s.title}</span>
                        {!m.content && <span className="source-snip">{s.snippet}</span>}
                      </button>
                    ))}
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>
          {asking && <div className="bubble assistant muted"><Spinner /> {t("docs.thinking")}</div>}
          <div ref={end} />
        </div>
      )}
      <form className="chat-input" onSubmit={ask}>
        <input className="field" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={t("docs.chatPlaceholder")} aria-label={t("docs.chatTitle")} />
        <button className="icon-btn send" disabled={asking || !question.trim()} aria-label={t("docs.chatTitle")}><Icon name="send" size={19} /></button>
      </form>
      <p className="faint small-text" style={{ margin: "8px 2px 0" }}>{ai ? t("docs.chatHint") : t("docs.chatNoAI")}</p>
    </Glass>
  );
}

function DocSheet({ doc, accounts, onClose, onChanged, onDeleted, notify }: {
  doc: Doc | null; accounts: Account[]; onClose: () => void; onChanged: (d: Doc) => void; onDeleted: () => void; notify: (s: string) => void;
}) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [target, setTarget] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setTarget(doc?.account_id ?? ""); }, [doc]);
  if (!doc) return <Sheet open={false} onClose={onClose}><span /></Sheet>;
  const look = LOOK[doc.kind];
  const canImport = /\.(pdf|csv|tsv|txt|ofx|qfx)$/i.test(doc.filename);

  const patch = async (body: Parameters<typeof api.patchDocument>[1]) => {
    try { onChanged(await api.patchDocument(doc.id, body)); notify(t("docs.saved")); } catch (e) { notify(errorText(e)); }
  };
  const importNow = async () => {
    setBusy(true);
    try {
      const r = await api.importDocument(doc.id, target || undefined);
      notify(t("docs.imported", { title: r.title, count: r.import?.added ?? 0 }));
      onChanged(await api.document(doc.id));
    } catch (e) { notify(errorText(e)); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (!confirm(t("docs.deleteConfirm"))) return;
    try { await api.deleteDocument(doc.id); onDeleted(); } catch (e) { notify(errorText(e)); }
  };

  return (
    <Sheet open onClose={onClose} title={doc.title} wide>
      <div className="stack">
        <div className="row" style={{ gap: 14 }}>
          <Tile icon={look.icon} from={look.colors[0]} to={look.colors[1]} size={48} />
          <div className="row-text">
            <span className="row-title">{t(kindKey(doc.kind))}{doc.issuer ? ` · ${doc.issuer}` : ""}</span>
            <span className="row-detail">{[doc.date && fmt.date(doc.date, { day: "numeric", month: "long", year: "numeric" }), doc.filename].filter(Boolean).join(" · ")}</span>
          </div>
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          <a className="btn ghost small" href={api.documentUrl(doc.id)} target="_blank" rel="noreferrer"><Icon name="open" size={16} /> {t("docs.open")}</a>
          <a className="btn ghost small" href={api.documentUrl(doc.id, true)}><Icon name="download" size={16} /> {t("docs.download")}</a>
          <button className="btn ghost small danger" onClick={remove}><Icon name="trash" size={16} /> {t("docs.delete")}</button>
        </div>
        {doc.review.account_warning && <AccountWarning doc={doc} accounts={accounts} notify={notify} onChanged={onChanged} />}
        {doc.status === "review" && <ReviewCard doc={doc} accounts={accounts} target={target} notify={notify} onChanged={onChanged} />}
        {doc.status === "processing" && (
          <div className="note row between wrap" style={{ gap: 10 }}>
            <span className="row" style={{ gap: 8 }}><Spinner /> {t("docs.readingLong")}</span>
            <button className="btn ghost small" onClick={async () => { try { onChanged(await api.stopDocument(doc.id)); } catch (e) { notify(errorText(e)); } }}>{t("docs.stop")}</button>
          </div>
        )}
        {doc.status === "error" && (
          <div className="note row between wrap" style={{ gap: 10 }}>
            <span>{t(`docs.failed.${["stopped", "interrupted"].includes(String(doc.review.error)) ? String(doc.review.error) : "other"}` as never)}</span>
            <button className="btn small" onClick={async () => { try { onChanged(await api.reprocessDocument(doc.id)); } catch (e) { notify(errorText(e)); } }}>{t("docs.reprocess")}</button>
          </div>
        )}
        {!doc.has_text && doc.status !== "processing" && <p className="note"><Icon name="shield" size={18} /> {t("docs.noText")}</p>}
        {doc.summary && <div><h4 className="section-label">{t("docs.summary")}</h4><p style={{ margin: 0 }}>{doc.summary}</p></div>}
        {doc.import_note === "account_not_recognised" && doc.facts.proposed_account && (
          <NewAccountCard doc={doc} proposed={doc.facts.proposed_account} notify={notify} onDone={async () => onChanged(await api.document(doc.id))} />
        )}
        {Object.keys(doc.facts).filter((k) => k !== "proposed_account").length > 0 && (
          <div>
            <h4 className="section-label">{t("docs.facts")}</h4>
            <div className="list">
              {Object.entries(doc.facts).filter(([k]) => k !== "proposed_account").map(([k, v]) => (
                <div key={k} className="item"><span className="item-main"><span className="row-text"><span className="row-detail">{k.replace(/_/g, " ")}</span></span><span className="num">{String(v)}</span></span></div>
              ))}
            </div>
          </div>
        )}
        {canImport && (
          <div>
            <h4 className="section-label">{t("docs.account")}</h4>
            {doc.imported != null && doc.account_id
              ? <p className="muted" style={{ margin: "0 0 10px" }}>{t("docs.importedInto", { count: doc.imported, account: accounts.find((a) => a.id === doc.account_id)?.name ?? "" })}</p>
              : doc.import_note && <p className="muted" style={{ margin: "0 0 10px" }}>{errorText(new Error(doc.import_note))}</p>}
            <div className="row wrap" style={{ gap: 8 }}>
              <select className="field" style={{ flex: 1, minWidth: 180 }} value={target} onChange={(e) => setTarget(e.target.value ? Number(e.target.value) : "")} aria-label={t("docs.importTo")}>
                <option value="">{t("docs.pickAccount")}</option>
                {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
              <button className="btn" onClick={importNow} disabled={busy}>{busy ? <Spinner /> : <Icon name="download" size={16} />} {t("docs.import")}</button>
            </div>
          </div>
        )}
        <div className="two">
          <Field label={t("docs.titleLabel")}><input className="field" defaultValue={doc.title} onBlur={(e) => e.target.value.trim() && e.target.value !== doc.title && patch({ title: e.target.value.trim() })} /></Field>
          <Field label={t("docs.kindLabel")}>
            <select className="field" value={doc.kind} onChange={(e) => patch({ kind: e.target.value as DocKind })}>
              {KINDS.map((k) => <option key={k} value={k}>{t(kindKey(k))}</option>)}
            </select>
          </Field>
        </div>
      </div>
    </Sheet>
  );
}

/** A statement of an account Oink doesn't know: add it, filled in from the statement. */
function NewAccountCard({ doc, proposed, notify, onDone }: { doc: Doc; proposed: ProposedAccount; notify: (s: string) => void; onDone: () => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [form, setForm] = useState(proposed);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof ProposedAccount) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const create = async () => {
    setBusy(true);
    try {
      const acc = await api.addAccount({ name: form.name, institution: form.institution, iban: form.iban, kind: form.kind, currency: form.currency });
      const r = await api.importDocument(doc.id, acc.id);
      notify(t("docs.imported", { title: acc.name, count: r.import?.added ?? 0 }));
      onDone();
    } catch (e) { notify(errorText(e)); } finally { setBusy(false); }
  };
  return (
    <div className="new-account">
      <p style={{ margin: 0 }}><strong>{t("docs.newAccountTitle")}</strong></p>
      <p className="muted small-text" style={{ margin: 0 }}>
        {t("docs.newAccountHint", { count: proposed.movements })}
        {proposed.balance ? ` ${t("docs.newAccountBalance", { amount: fmt.money(proposed.balance, proposed.currency) })}` : ""}
      </p>
      <div className="two">
        <Field label={t("docs.newAccountName")}><input className="field" value={form.name} onChange={set("name")} /></Field>
        <Field label={t("docs.issuer")}><input className="field" value={form.institution} onChange={set("institution")} /></Field>
        <Field label="IBAN"><input className="field mono" value={form.iban} onChange={set("iban")} /></Field>
        <Field label={t("docs.kindLabel")}>
          <select className="field" value={form.kind} onChange={set("kind")}>
            {["checking", "savings", "card", "cash", "investment", "crypto"].map((k) => <option key={k} value={k}>{t(`accounts.kind.${k}` as Key)}</option>)}
          </select>
        </Field>
      </div>
      <button className="btn" onClick={create} disabled={busy || !form.name.trim()}>{busy ? <Spinner /> : <Icon name="plus" size={16} />} {t("docs.newAccountCreate")}</button>
    </div>
  );
}

/** What didn't add up, what the AI thinks of it, and the person's decision. */
function ReviewCard({ doc, accounts, target, notify, onChanged }: {
  doc: Doc; accounts: Account[]; target: number | ""; notify: (s: string) => void; onChanged: (d: Doc) => void;
}) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [busy, setBusy] = useState("");
  const money = (v?: string) => (v == null ? "—" : fmt.money(v, accounts.find((a) => a.id === doc.account_id)?.currency ?? "EUR"));
  const act = async (what: string, run: () => Promise<Doc>) => {
    setBusy(what);
    try { onChanged(await run()); notify(t("docs.saved")); } catch (e) { notify(errorText(e)); } finally { setBusy(""); }
  };
  const ai = doc.review.ai;
  return (
    <div className="review-card">
      <p style={{ margin: 0 }}><strong>{t("docs.reviewTitle")}</strong></p>
      {(doc.review.checks ?? []).filter((c) => c.ok !== true).map((c) => (
        <p key={c.name} className="small-text" style={{ margin: 0 }}>
          {c.ok === false && c.name === "statement_balance" ? t("docs.check.statementBalance", { opening: money(c.detail.opening), movements: money(c.detail.movements), expected: money(c.detail.expected), closing: money(c.detail.closing), difference: money(c.detail.difference) })
            : c.ok === false && c.name === "payslip_net" ? t("docs.check.payslipNet", { gross: money(c.detail.gross), deductions: money(c.detail.deductions), expected: money(c.detail.expected), net: money(c.detail.net) })
            : c.ok === false && c.name === "invoice_total" ? t("docs.check.invoiceTotal", { expected: money(c.detail.expected), total: money(c.detail.total) })
            : t(`docs.check.reason.${c.detail.reason ?? "unknown"}` as Key)}
        </p>
      ))}
      {ai?.explanation && (
        <div className="ai-note">
          <span className="faint small-text row" style={{ gap: 6 }}><Icon name="sparkle" size={14} /> {t("docs.aiNote")}</span>
          <p style={{ margin: "4px 0 0" }}>{ai.explanation}</p>
          {ai.suggestions && ai.suggestions.length > 0 && <ul>{ai.suggestions.map((x, i) => <li key={i}>{x}</li>)}</ul>}
        </div>
      )}
      {doc.rows && doc.rows.length > 0 && (
        <details>
          <summary className="small-text">{t("docs.rowsRead", { count: doc.rows.length })}</summary>
          <div className="rows-read">
            {doc.rows.slice(0, 200).map(([day, amount, text], i) => (
              <div key={i} className="row between small-text"><span className="faint">{day}</span><span className="ellipsis" style={{ flex: 1, margin: "0 10px" }}>{text}</span><span className="num">{amount}</span></div>
            ))}
          </div>
        </details>
      )}
      <div className="row wrap" style={{ gap: 8 }}>
        {doc.kind === "statement" && (doc.rows?.length ?? 0) > 0 && (
          <button className="btn small" disabled={!!busy} onClick={() => act("import", async () => { const r = await api.importDocument(doc.id, target || undefined); return r; })}>
            {busy === "import" ? <Spinner /> : null} {t("docs.importAnyway")}</button>
        )}
        <button className="btn ghost small" disabled={!!busy} onClick={() => act("dismiss", () => api.dismissDocument(doc.id))}>{t("docs.dismiss")}</button>
        <button className="btn ghost small" disabled={!!busy} onClick={() => act("again", () => api.reprocessDocument(doc.id))}>{busy === "again" ? <Spinner /> : null} {t("docs.reprocess")}</button>
      </div>
    </div>
  );
}

/** «This statement looks like another account's»: say why, offer to move it. */
function AccountWarning({ doc, accounts, notify, onChanged }: { doc: Doc; accounts: Account[]; notify: (s: string) => void; onChanged: (d: Doc) => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const w = doc.review.account_warning!;
  const current = accounts.find((a) => a.id === doc.account_id);
  const [to, setTo] = useState<number | "">(w.suggested ?? "");
  const move = async () => {
    if (!to) return;
    try { await api.moveDocument(doc.id, to); onChanged(await api.document(doc.id)); notify(t("docs.moved")); } catch (e) { notify(errorText(e)); }
  };
  return (
    <div className="review-card">
      <p style={{ margin: 0 }}><strong>{t(`docs.warn.${w.code}` as Key, { account: current?.name ?? "", found: w.found ?? "" })}</strong></p>
      <div className="row wrap" style={{ gap: 8 }}>
        <select className="field" style={{ flex: 1, minWidth: 180 }} value={to} onChange={(e) => setTo(e.target.value ? Number(e.target.value) : "")}>
          <option value="">{t("docs.pickAccount")}</option>
          {accounts.filter((a) => a.id !== doc.account_id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <button className="btn small" disabled={!to} onClick={move}>{t("docs.move")}</button>
      </div>
    </div>
  );
}
