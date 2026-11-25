/* Accounts: the cards, the links (banks, exchanges, wallets), statements in, and the
   «add an account» wizard. */

import { motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Account, type Connection, type ImportResult, type KeyedProvider, type Providers, type StatementRequest } from "../api";
import { Glass } from "../glass/Glass";
import { useErrorText, useI18n } from "../i18n";
import { Empty, Field, Money, PageHeader, Row, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { Icon, KIND_COLORS, Tile } from "../ui/icons";
import { KINDS, kindLabel } from "../ui/labels";

export function Accounts({ notify, adding, setAdding, baseCurrency }: { notify: (s: string) => void; adding: boolean; setAdding: (v: boolean) => void; baseCurrency: string }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [archived, setArchived] = useState<Account[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [requests, setRequests] = useState<StatementRequest[]>([]);
  const [total, setTotal] = useState<{ total: number; currency: string } | null>(null);
  const [open, setOpen] = useState<Account | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [params, setParams] = useSearchParams();

  const load = useCallback(async () => {
    const [acc, arch, conns, reqs, overview] = await Promise.all([
      api.accounts(), api.accounts(true), api.connections(), api.requests(), api.overview(),
    ]);
    setAccounts(acc);
    setArchived(arch.filter((a) => a.archived));
    setConnections(conns);
    setRequests(reqs);
    setTotal({ total: overview.net_worth.total, currency: overview.currency });
  }, []);
  useEffect(() => { load().catch((e) => notify(errorText(e))); }, [load, notify, errorText]);
  useEffect(() => {
    const linked = params.get("linked");
    if (!linked) return;
    notify(t(linked === "ok" ? "accounts.linkedOk" : "accounts.linkedError"));
    setParams({}, { replace: true });
  }, [params, setParams, notify, t]);

  const sync = async () => {
    setSyncing(true);
    try {
      const { added } = await api.sync();
      notify(t("accounts.synced", { count: added }));
      await load();
    } catch (e) {
      notify(errorText(e));
    } finally {
      setSyncing(false);
    }
  };

  const imported = (r: ImportResult) => {
    const parts = [t("accounts.imported", { count: r.added })];
    if (r.duplicates) parts.push(t("accounts.duplicates", { count: r.duplicates }));
    notify(parts.join(" · ") + (r.estimated ? ` — ${t("accounts.estimated")}` : ""));
    load();
  };

  return (
    <motion.div className="page" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("accounts.title")}
        subtitle={total ? <span className="num">{t("accounts.total")} {fmt.money(total.total, total.currency || baseCurrency)}</span> : undefined}
        action={
          <div className="row" style={{ gap: 8 }}>
            {connections.length > 0 && <button className="icon-btn glassy" onClick={sync} disabled={syncing} aria-label={t("accounts.syncNow")} title={t("accounts.syncNow")}>{syncing ? <Spinner /> : <Icon name="sync" />}</button>}
            <button className="btn" onClick={() => setAdding(true)}><Icon name="plus" size={18} /> <span className="hide-xs">{t("accounts.add")}</span></button>
          </div>
        } />

      {requests.length > 0 && accounts && (
        <motion.div variants={rise} className="stack">
          {requests.map((r) => {
            const acc = accounts.find((a) => a.id === r.account_id);
            return (
              <Glass key={r.id} className="banner" variant="tight">
                <Icon name="inbox" />
                <span className="row-text">
                  <span className="row-title ellipsis">{acc?.name}</span>
                  <span className="row-detail">{t("accounts.requestFor", { month: fmt.month(r.month) })}</span>
                </span>
                <span className="row" style={{ gap: 6, flex: "none" }}>
                  <button className="btn ghost small" onClick={() => acc && setOpen(acc)}>{t("home.upload")}</button>
                  <button className="btn ghost small" onClick={() => api.skipRequest(r.id).then(load)}>{t("accounts.skip")}</button>
                </span>
              </Glass>
            );
          })}
        </motion.div>
      )}

      {accounts === null ? (
        <div className="cards"><div className="skeleton" style={{ height: 160 }} /><div className="skeleton" style={{ height: 160 }} /></div>
      ) : accounts.length === 0 ? (
        <Glass className="card"><Empty icon="wallet" title={t("accounts.empty")} hint={t("home.emptyHint")} action={<button className="btn" onClick={() => setAdding(true)}>{t("accounts.add")}</button>} /></Glass>
      ) : (
        <motion.div variants={rise} className="cards">
          {accounts.map((a, i) => <AccountCard key={a.id} account={a} index={i} onOpen={() => setOpen(a)} />)}
        </motion.div>
      )}

      {accounts && accounts.length > 0 && (
        <motion.div variants={rise}><DropZone onDone={imported} hint={t("accounts.dropAny")} /></motion.div>
      )}

      {connections.length > 0 && (
        <motion.div variants={rise}>
          <Glass className="card">
            <h3>{t("accounts.connections")}</h3>
            <div className="list">
              {connections.map((c) => (
                <Row key={c.id} icon={<Tile icon={c.provider === "enablebanking" ? "bank" : c.provider === "bitcoin" || c.provider === "ethereum" ? "crypto" : "exchange"} from="#9b8cff" to="#5a45d6" size={36} />}
                  title={c.label}
                  detail={<span className={c.status === "active" ? "" : "down"}>{t(`accounts.status.${c.status}` as never)}{c.last_sync ? ` · ${t("accounts.lastSync", { when: fmt.relative(c.last_sync) })}` : ""}</span>}
                  right={<button className="btn ghost small" onClick={async () => { if (confirm(t("accounts.unlinkHint"))) { await api.unlink(c.id); load(); } }}>{t("accounts.unlink")}</button>} />
              ))}
            </div>
          </Glass>
        </motion.div>
      )}

      {archived.length > 0 && (
        <motion.div variants={rise}>
          <details className="archived">
            <summary className="muted">{t("accounts.archived")} ({archived.length})</summary>
            <Glass className="card list">
              {archived.map((a) => <Row key={a.id} title={a.name} detail={a.institution} onClick={() => setOpen(a)} chevron />)}
            </Glass>
          </details>
        </motion.div>
      )}

      <AccountSheet account={open} onClose={() => setOpen(null)} onChanged={() => { setOpen(null); load(); }} onImported={(r) => { setOpen(null); imported(r); }} />
      <AddAccount open={adding} onClose={() => setAdding(false)} onDone={(text) => { setAdding(false); notify(text); load(); }} />
    </motion.div>
  );
}

function AccountCard({ account, index, onOpen }: { account: Account; index: number; onOpen: () => void }) {
  const { t, fmt } = useI18n();
  const [a, b] = KIND_COLORS[account.kind] ?? KIND_COLORS.checking;
  return (
    <Glass refract strength={36} press className="wallet-card" onClick={onOpen}
      style={{ background: `linear-gradient(150deg, ${a}cc, ${b}aa)` }}
      initial={{ opacity: 0, y: 20, rotate: -1.5 }} animate={{ opacity: 1, y: 0, rotate: 0 }} transition={{ type: "spring", stiffness: 200, damping: 20, delay: index * 0.05 }}>
      <div className="row between">
        <span className="row" style={{ gap: 8, minWidth: 0 }}>
          <Icon name={account.kind === "investment" ? "investment" : account.kind} size={20} />
          <span className="ellipsis strong">{account.name}</span>
        </span>
        <span className="badge">{account.source === "linked" ? <><Icon name="link" size={13} /> {t("accounts.linked")}</> : t("accounts.manual")}</span>
      </div>
      <div>
        <div className="mid"><Money value={account.balance} currency={account.currency} /></div>
        <div className="small-text dim">{account.institution || kindLabel(t, account.kind)}{account.balance_at ? ` · ${fmt.relative(account.balance_at)}` : ""}</div>
      </div>
    </Glass>
  );
}

export function DropZone({ accountId, onDone, hint }: { accountId?: number; onDone: (r: ImportResult) => void; hint?: string }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) onDone(await api.importFile(file, accountId));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <div>
      <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0}
        onClick={() => input.current?.click()} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
        {busy ? <Spinner /> : <Icon name="upload" size={28} />}
        <strong>{t("accounts.drop")}</strong>
        <span className="muted small-text">{hint ?? t("accounts.dropHint")}</span>
        <input ref={input} type="file" accept=".csv,.tsv,.txt,.pdf,.ofx,.qfx" multiple hidden onChange={(e) => send(e.target.files)} />
      </div>
      {error && <div className="error" style={{ marginTop: 10 }}>{error}</div>}
    </div>
  );
}

function AccountSheet({ account, onClose, onChanged, onImported }: { account: Account | null; onClose: () => void; onChanged: () => void; onImported: (r: ImportResult) => void }) {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [name, setName] = useState("");
  const [balance, setBalance] = useState("");
  const [iban, setIban] = useState("");
  const [kind, setKind] = useState("checking");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!account) return;
    setName(account.name); setBalance(account.balance ?? ""); setIban(account.iban); setKind(account.kind); setError("");
  }, [account]);
  if (!account) return <Sheet open={false} onClose={onClose}>{null}</Sheet>;
  const manual = account.source === "manual";

  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api.patchAccount(account.id, { name, iban, kind, ...(manual ? { balance: balance.replace(",", ".") || null } : {}) });
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Sheet open={!!account} onClose={onClose} title={account.name}>
      <div className="tx-hero">
        <div className="big-amount num">{fmt.money(account.balance, account.currency)}</div>
        <div className="muted">{account.balance_at ? t("accounts.balanceAt", { when: fmt.date(account.balance_at, { day: "numeric", month: "long", year: "numeric" }) }) : kindLabel(t, account.kind)}</div>
      </div>
      {!account.archived && <DropZone accountId={account.id} onDone={onImported} />}
      <form className="stack" onSubmit={save} style={{ marginTop: 16 }}>
        <Field label={t("accounts.name")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} required /></Field>
        <div className="two">
          <Field label={t("accounts.kind")}>
            <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => <option key={k} value={k}>{kindLabel(t, k)}</option>)}
            </select>
          </Field>
          {manual && <Field label={`${t("accounts.balance")} (${account.currency})`}><input className="field num" inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} /></Field>}
        </div>
        <Field label={t("accounts.iban")} hint={t("accounts.ibanHint")}><input className="field mono" value={iban} onChange={(e) => setIban(e.target.value.toUpperCase())} autoCapitalize="characters" /></Field>
        {error && <div className="error">{error}</div>}
        <button className="btn big-btn">{t("common.save")}</button>
        <div className="row between">
          <button type="button" className="btn ghost" onClick={async () => { await api.patchAccount(account.id, { archived: !account.archived }); onChanged(); }}>
            {account.archived ? t("accounts.unarchive") : t("accounts.archive")}
          </button>
          <button type="button" className="btn ghost danger" onClick={async () => { if (confirm(t("common.confirmDelete"))) { await api.deleteAccount(account.id); onChanged(); } }}>
            <Icon name="trash" size={18} /> {t("common.delete")}
          </button>
        </div>
      </form>
    </Sheet>
  );
}

type Step = "choose" | "bank" | "exchange" | "wallet" | "fintech" | "manual";

function AddAccount({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: (text: string) => void }) {
  const { t } = useI18n();
  const [step, setStep] = useState<Step>("choose");
  const [providers, setProviders] = useState<Providers | null>(null);
  useEffect(() => { if (open) { setStep("choose"); api.providers().then(setProviders).catch(() => undefined); } }, [open]);

  const options: { step: Step; icon: string; title: string; hint: string; colors: [string, string] }[] = [
    { step: "bank", icon: "bank", title: t("wizard.bank"), hint: t("wizard.bankHint"), colors: ["#ff8cc0", "#c94b8c"] },
    { step: "exchange", icon: "exchange", title: t("wizard.exchange"), hint: t("wizard.exchangeHint"), colors: ["#ffc46b", "#e07a12"] },
    { step: "wallet", icon: "crypto", title: t("wizard.wallet"), hint: t("wizard.walletHint"), colors: ["#6ee7d0", "#13897e"] },
    { step: "fintech", icon: "fintech", title: t("wizard.fintech"), hint: t("wizard.fintechHint"), colors: ["#9be15d", "#4f9a1d"] },
    { step: "manual", icon: "manual", title: t("wizard.manual"), hint: t("wizard.manualHint"), colors: ["#9b8cff", "#5a45d6"] },
  ];
  const keyed = (kind: string) => providers?.keyed.filter((p) => p.kind === kind) ?? [];

  return (
    <Sheet open={open} onClose={onClose} title={t("wizard.title")}>
      {step !== "choose" && <button className="link back" onClick={() => setStep("choose")}><Icon name="back" size={16} /> {t("common.back")}</button>}
      <motion.div key={step} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} transition={{ type: "spring", stiffness: 300, damping: 30 }}>
        {step === "choose" && (
          <div className="list wizard">
            {options.map((o) => <Row key={o.step} icon={<Tile icon={o.icon} from={o.colors[0]} to={o.colors[1]} size={44} />} title={o.title} detail={o.hint} onClick={() => setStep(o.step)} chevron />)}
          </div>
        )}
        {step === "bank" && <BankStep providers={providers} />}
        {(step === "exchange" || step === "wallet" || step === "fintech") && <KeyStep providers={keyed(step)} onDone={onDone} />}
        {step === "manual" && <ManualStep onDone={onDone} />}
      </motion.div>
    </Sheet>
  );
}

function Directory({ providers, onSearch }: { providers: Providers; onSearch: (country: string, q: string) => void }) {
  const { t } = useI18n();
  const [picked, setPicked] = useState<string>("");
  const entry = providers.directory.find((d) => d.name === picked);
  return (
    <div className="stack">
      <h4 className="section-label">{t("wizard.popular")}</h4>
      <div className="chips wrap-chips">
        {providers.directory.filter((d) => d.method !== "key").map((d) => (
          <button key={d.name} className={`chip ${picked === d.name ? "on" : ""}`}
            onClick={() => { setPicked(d.name); if (d.search && d.method !== "statement") onSearch(d.country, d.search); }}>{d.name}</button>
        ))}
      </div>
      {entry && (
        <p className="note"><Icon name={entry.method === "statement" ? "upload" : "bank"} size={18} />
          <span>{t(`wizard.method.${entry.method}` as never)}{entry.export ? ` — ${t("wizard.export", { how: entry.export })}` : ""}</span></p>
      )}
    </div>
  );
}

function BankStep({ providers }: { providers: Providers | null }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const guess = (navigator.language.split("-")[1] ?? "IT").toUpperCase();
  const [country, setCountry] = useState(guess);
  const [banks, setBanks] = useState<{ name: string; logo: string }[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [going, setGoing] = useState("");
  useEffect(() => {
    if (!providers?.banking) return;
    if (!providers.countries.includes(country)) { setCountry("IT"); return; }
    setBanks(null);
    api.banks(country).then(setBanks).catch((e) => setError(errorText(e)));
  }, [providers, country, errorText]);
  if (!providers) return <Spinner />;
  if (!providers.banking) return <div className="stack"><p className="muted">{t("wizard.bankOff")}</p><Directory providers={providers} onSearch={() => undefined} /></div>;
  const shown = (banks ?? []).filter((b) => b.name.toLowerCase().includes(q.toLowerCase())).slice(0, 60);
  const go = async (bank: string) => {
    setGoing(bank);
    try {
      const { url } = await api.linkBank(bank, country);
      window.location.href = url;
    } catch (e) {
      setError(errorText(e));
      setGoing("");
    }
  };
  return (
    <div className="stack">
      <Directory providers={providers} onSearch={(c, text) => { if (c) setCountry(c); setQ(text); }} />
      <div className="two">
        <Field label={t("wizard.country")}>
          <select className="field" value={country} onChange={(e) => setCountry(e.target.value)}>
            {providers.countries.map((c) => <option key={c} value={c}>{new Intl.DisplayNames([navigator.language], { type: "region" }).of(c)}</option>)}
          </select>
        </Field>
        <Field label={t("common.search")}><input className="field" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("wizard.searchBank")} /></Field>
      </div>
      {error && <div className="error">{error}</div>}
      {banks === null ? <Spinner /> : (
        <div className="list bank-list">
          {shown.map((b) => (
            <Row key={b.name} icon={b.logo ? <img src={b.logo} alt="" className="bank-logo" /> : <Tile icon="bank" from="#ff8cc0" to="#c94b8c" size={36} />}
              title={b.name} detail={going === b.name ? t("wizard.redirect", { bank: b.name }) : undefined}
              right={going === b.name ? <Spinner /> : undefined} onClick={() => go(b.name)} chevron />
          ))}
        </div>
      )}
    </div>
  );
}

function KeyStep({ providers, onDone }: { providers: KeyedProvider[]; onDone: (text: string) => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [chosen, setChosen] = useState<KeyedProvider | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (providers.length === 1) setChosen(providers[0]); }, [providers]);
  if (!chosen) {
    return (
      <div className="list">
        {providers.map((p) => <Row key={p.id} icon={<Tile icon={p.kind === "wallet" ? "crypto" : "exchange"} from="#ffc46b" to="#e07a12" size={40} />} title={p.name} detail={p.movements ? t("wizard.movements") : t("wizard.balancesOnly")} onClick={() => setChosen(p)} chevron />)}
      </div>
    );
  }
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.linkKey(chosen.id, values, label || chosen.name);
      const accounts = await api.accounts();
      onDone(t("wizard.connected", { count: accounts.filter((a) => a.source === "linked").length }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <div className="row between">
        <strong className="mid-text">{chosen.name}</strong>
        <span className="badge soft">{chosen.movements ? t("wizard.movements") : t("wizard.balancesOnly")}</span>
      </div>
      {chosen.kind !== "wallet" && <p className="note"><Icon name="shield" size={18} /> {t("wizard.readOnly")}</p>}
      {chosen.fields.map((f) => (
        <Field key={f.name} label={t(`wizard.field.${f.name}` as never)}>
          <input className="field mono" type={f.secret ? "password" : "text"} autoComplete="off" spellCheck={false} placeholder={f.placeholder}
            value={values[f.name] ?? ""} onChange={(e) => setValues({ ...values, [f.name]: e.target.value })} required />
        </Field>
      ))}
      <Field label={`${t("wizard.label")} (${t("common.optional")})`}><input className="field" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={chosen.name} /></Field>
      <a className="link" href={chosen.docs} target="_blank" rel="noreferrer noopener"><Icon name="key" size={16} /> {t("wizard.whereKey")}</a>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" disabled={busy}>{busy ? <><Spinner /> {t("wizard.connecting")}</> : t("wizard.connect")}</button>
    </form>
  );
}

function ManualStep({ onDone }: { onDone: (text: string) => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [currencies, setCurrencies] = useState<{ fiat: string[]; crypto: string[] }>({ fiat: ["EUR"], crypto: [] });
  const [name, setName] = useState("");
  const [kind, setKind] = useState("checking");
  const [currency, setCurrency] = useState("EUR");
  const [balance, setBalance] = useState("");
  const [institution, setInstitution] = useState("");
  const [iban, setIban] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api.currencies().then(setCurrencies).catch(() => undefined); }, []);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const acc = await api.addAccount({ name, kind, currency, institution, iban, balance: balance.replace(",", ".") || undefined });
      onDone(acc.name);
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <Field label={t("accounts.name")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} required autoFocus /></Field>
      <div className="two">
        <Field label={t("accounts.kind")}>
          <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{kindLabel(t, k)}</option>)}
          </select>
        </Field>
        <Field label={t("accounts.currency")}>
          <select className="field" value={currency} onChange={(e) => setCurrency(e.target.value)}>
            <optgroup label="Fiat">{currencies.fiat.map((c) => <option key={c}>{c}</option>)}</optgroup>
            <optgroup label="Crypto">{currencies.crypto.map((c) => <option key={c}>{c}</option>)}</optgroup>
          </select>
        </Field>
      </div>
      <Field label={t("wizard.openingBalance")}><input className="field num" inputMode="decimal" value={balance} onChange={(e) => setBalance(e.target.value)} placeholder="0.00" /></Field>
      <Field label={`${t("accounts.institution")} (${t("common.optional")})`}><input className="field" value={institution} onChange={(e) => setInstitution(e.target.value)} /></Field>
      <Field label={`${t("accounts.iban")} (${t("common.optional")})`} hint={t("accounts.ibanHint")}><input className="field mono" value={iban} onChange={(e) => setIban(e.target.value.toUpperCase())} /></Field>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("common.add")}</button>
    </form>
  );
}
