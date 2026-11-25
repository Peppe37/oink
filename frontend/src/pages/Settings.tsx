/* Settings, grouped like a phone's: profile, password, connected apps, and for the admin
   people, bank access and the system. */

import { motion } from "framer-motion";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type AISettings, type SystemInfo, type Token, type User } from "../api";
import { Glass } from "../glass/Glass";
import { LANGUAGES, useErrorText, useI18n } from "../i18n";
import { Field, PageHeader, Row, Sheet, Spinner, rise, stagger } from "../ui/bits";
import { Icon, Tile } from "../ui/icons";

type Panel = "profile" | "password" | "apps" | "users" | "bank" | "system" | "ai" | "mcp" | null;

export function Settings({ user, onUser, onLogout, notify, onUpdate }: { user: User; onUser: (u: User) => void; onLogout: () => void; notify: (s: string) => void; onUpdate: () => void }) {
  const { t, lang } = useI18n();
  const [panel, setPanel] = useState<Panel>(null);
  const [system, setSystem] = useState<SystemInfo | null>(null);
  useEffect(() => { api.system().then(setSystem).catch(() => undefined); }, []);
  const close = () => setPanel(null);
  const item = (p: Panel, icon: string, colors: [string, string], title: string, detail?: ReactNode) => (
    <Row icon={<Tile icon={icon} from={colors[0]} to={colors[1]} size={34} />} title={title} detail={detail} onClick={() => setPanel(p)} chevron />
  );
  const update = system?.updates;

  return (
    <motion.div className="page narrow" variants={stagger} initial="hidden" animate="show">
      <PageHeader title={t("settings.title")} />
      <motion.div variants={rise}>
        <Glass className="card profile-card">
          <span className="avatar">{(user.name || user.username).slice(0, 1).toUpperCase()}</span>
          <div className="ellipsis"><strong>{user.name || user.username}</strong><div className="muted small-text">@{user.username}{user.is_admin ? ` · ${t("settings.admin")}` : ""}</div></div>
        </Glass>
      </motion.div>
      <motion.div variants={rise}>
        <Glass className="card list group">
          {item("profile", "user", ["#ff8cc0", "#c94b8c"], t("settings.profile"), `${LANGUAGES[lang]} · ${user.base_currency}`)}
          {item("password", "key", ["#ffd76a", "#f2a71b"], t("settings.security"))}
          {item("apps", "link", ["#6cc9ff", "#2f86e8"], t("settings.apps"))}
          {item("mcp", "sparkle", ["#cf95ff", "#9550e8"], t("settings.mcp"))}
        </Glass>
      </motion.div>
      {user.is_admin && (
        <motion.div variants={rise}>
          <Glass className="card list group">
            {item("users", "users", ["#a08bff", "#6a4fe6"], t("settings.users"))}
            {item("ai", "insights", ["#ffd27a", "#f08a1c"], t("settings.ai"), system?.ai_model || t("settings.aiOff"))}
            {item("bank", "bank", ["#43e5b5", "#0fa37f"], t("settings.bank"), system?.banking ? t("settings.bankReady") : undefined)}
            {item("system", "download", ["#b3aec2", "#7c7690"], t("settings.system"),
              update?.update_available ? <span className="accent">{t("settings.updateAvailable", { version: update.latest?.version ?? "" })}</span> : `${t("settings.version")} ${system?.version ?? ""}`)}
          </Glass>
        </motion.div>
      )}
      <motion.div variants={rise}>
        <Glass className="card list group">
          <Row icon={<Tile icon="logout" from="#ff8aa0" to="#e8456a" size={34} />} title={t("auth.signOut")} onClick={async () => { await api.logout(); onLogout(); }} />
        </Glass>
      </motion.div>
      <p className="faint center-text small-text">Oink {system?.version ?? ""}</p>

      <Sheet open={panel === "profile"} onClose={close} title={t("settings.profile")}><ProfilePanel user={user} onUser={(u) => { onUser(u); close(); }} /></Sheet>
      <Sheet open={panel === "password"} onClose={close} title={t("settings.security")}><PasswordPanel onDone={() => { notify(t("settings.passwordChanged")); close(); }} /></Sheet>
      <Sheet open={panel === "apps"} onClose={close} title={t("settings.apps")} wide><AppsPanel /></Sheet>
      <Sheet open={panel === "users"} onClose={close} title={t("settings.users")} wide><UsersPanel me={user} /></Sheet>
      <Sheet open={panel === "bank"} onClose={close} title={t("settings.bank")}><BankPanel system={system} onDone={() => { notify(t("settings.bankSaved")); api.system().then(setSystem); close(); }} /></Sheet>
      <Sheet open={panel === "ai"} onClose={close} title={t("settings.ai")}><AIPanel notify={notify} onSaved={() => api.system().then(setSystem)} /></Sheet>
      <Sheet open={panel === "mcp"} onClose={close} title={t("settings.mcp")} wide><MCPPanel system={system} /></Sheet>
      <Sheet open={panel === "system"} onClose={close} title={t("settings.system")}><SystemPanel system={system} onUpdate={() => { close(); onUpdate(); }} /></Sheet>
    </motion.div>
  );
}

function ProfilePanel({ user, onUser }: { user: User; onUser: (u: User) => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [name, setName] = useState(user.name);
  const [language, setLanguage] = useState(user.language);
  const [currency, setCurrency] = useState(user.base_currency);
  const [currencies, setCurrencies] = useState<string[]>([user.base_currency]);
  const [error, setError] = useState("");
  useEffect(() => { api.currencies().then((c) => setCurrencies([...c.fiat, ...c.crypto])).catch(() => undefined); }, []);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { onUser(await api.updateMe({ name, language, base_currency: currency })); } catch (err) { setError(errorText(err)); }
  };
  return (
    <form className="stack" onSubmit={save}>
      <Field label={t("settings.name")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} /></Field>
      <Field label={t("settings.language")}>
        <select className="field" value={language} onChange={(e) => setLanguage(e.target.value)}>
          <option value="">{t("settings.systemLanguage")}</option>
          {Object.entries(LANGUAGES).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
        </select>
      </Field>
      {window.__oinkSSO && (
        <div className="row between note">
          <span>{user.sso ? t("settings.ssoLinked", { name: window.__oinkSSO }) : t("settings.ssoNotLinked", { name: window.__oinkSSO })}</span>
          {user.sso ? <button type="button" className="btn ghost small" onClick={() => api.unlinkSSO().then(() => window.location.reload()).catch((e) => setError(errorText(e)))}>{t("accounts.unlink")}</button>
            : <a className="btn ghost small" href="/api/auth/oidc/start?link=true">{t("settings.ssoLink")}</a>}
        </div>
      )}
      <Field label={t("settings.baseCurrency")} hint={t("settings.baseCurrencyHint")}>
        <select className="field" value={currency} onChange={(e) => setCurrency(e.target.value)}>{currencies.map((c) => <option key={c}>{c}</option>)}</select>
      </Field>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("common.save")}</button>
    </form>
  );
}

function AIPanel({ notify, onSaved }: { notify: (s: string) => void; onSaved: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [cfg, setCfg] = useState<AISettings | null>(null);
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [send, setSend] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api.aiSettings().then((c) => {
      setCfg(c); setModel(c.model); setBaseUrl(c.base_url); setSend(c.send_descriptions);
      const p = c.model.split("/")[0];
      if (p in c.providers) setProvider(p);
    }).catch((e) => setError(errorText(e)));
  }, [errorText]);
  if (!cfg) return <Spinner />;
  const save = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError("");
    try {
      setCfg(await api.setAI({ model, base_url: baseUrl, api_key: key ? key : null, send_descriptions: send }));
      setKey(""); onSaved();
      const r = await api.testAI();
      notify(t("settings.aiWorks", { reply: r.reply }));
    } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <form className="stack" onSubmit={save}>
      <p className="muted">{t("settings.aiHint")}</p>
      {cfg.source !== "off" && <p className="note"><Icon name="check" size={18} /> {t(cfg.source === "app" ? "settings.aiFromApp" : "settings.aiFromEnv", { model: cfg.model })}</p>}
      <div className="chips">
        {Object.keys(cfg.providers).map((p) => (
          <button type="button" key={p} className={`chip ${provider === p ? "on" : ""}`} onClick={() => { setProvider(p); setModel(cfg.providers[p].example); }}>{p}</button>
        ))}
      </div>
      <Field label={t("settings.aiModel")} hint={t("settings.aiModelHint")}><input className="field mono" value={model} onChange={(e) => setModel(e.target.value)} required placeholder={cfg.providers[provider]?.example} /></Field>
      {cfg.providers[provider]?.key !== false && (
        <Field label={t("settings.aiKey")} hint={cfg.has_key ? t("settings.aiKeySaved") : undefined}>
          <input className="field mono" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={cfg.has_key ? "••••••••" : "sk-…"} />
        </Field>
      )}
      <Field label={`${t("settings.aiBaseUrl")} (${t("common.optional")})`}><input className="field mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={provider === "ollama" ? "http://ollama:11434" : ""} /></Field>
      <label className="check"><input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} /> {t("settings.aiSend")}</label>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" disabled={busy}>{busy ? <Spinner /> : t("settings.aiSaveTest")}</button>
      {cfg.source === "app" && <button type="button" className="btn ghost" onClick={async () => { setCfg(await api.resetAI()); onSaved(); }}>{t("settings.aiReset")}</button>}
    </form>
  );
}

function MCPPanel({ system }: { system: SystemInfo | null }) {
  const { t } = useI18n();
  const origin = window.location.origin;
  const mcp = system?.mcp_url ?? `${origin}/mcp`;
  const snippet = JSON.stringify({ mcpServers: { oink: { type: "http", url: mcp, headers: { Authorization: "Bearer oink_…" } } } }, null, 2);
  return (
    <div className="stack">
      <p className="muted">{t("settings.mcpHint")}</p>
      <h4 className="section-label">MCP</h4>
      <Secret value={mcp} />
      <pre className="secret code">{snippet}</pre>
      <h4 className="section-label">REST API</h4>
      <p className="small-text muted">{t("settings.apiHint")}</p>
      <a className="link" href={system?.api_docs ?? "/api/docs"} target="_blank" rel="noreferrer"><Icon name="globe" size={16} /> {t("settings.apiDocs")}</a>
      <p className="small-text">{t("settings.mcpToken")}</p>
    </div>
  );
}

function PasswordPanel({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { await api.password(current, next); onDone(); } catch (err) { setError(errorText(err)); }
  };
  return (
    <form className="stack" onSubmit={save}>
      <input type="text" autoComplete="username" hidden readOnly />
      <Field label={t("settings.currentPassword")}><input className="field" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></Field>
      <Field label={t("settings.newPassword")} hint={t("auth.passwordHint")}><input className="field" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} /></Field>
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn">{t("settings.changePassword")}</button>
    </form>
  );
}

function Secret({ value }: { value: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  return (
    <div className="secret-box">
      <code className="secret">{value}</code>
      <button type="button" className="btn ghost small" onClick={() => { navigator.clipboard.writeText(value); setCopied(true); }}>
        <Icon name={copied ? "check" : "link"} size={16} /> {copied ? t("common.copied") : t("common.copy")}
      </button>
    </div>
  );
}

function AppsPanel() {
  const { t, fmt } = useI18n();
  const errorText = useErrorText();
  const [tokens, setTokens] = useState<Token[]>([]);
  const [scopes, setScopes] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const load = () => api.tokens().then((all) => setTokens(all.filter((tk) => !tk.revoked)));
  useEffect(() => { load(); api.scopes().then((s) => { setScopes(s); setChosen(s.filter((x) => x.endsWith(":read"))); }); }, []);
  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const token = await api.newToken(name, chosen);
      setSecret(token.token);
      setName("");
      load();
    } catch (err) { setError(errorText(err)); }
  };
  return (
    <div className="stack">
      <p className="muted">{t("settings.appsHint")}</p>
      {secret && <div className="stack"><strong>{t("settings.tokenCreated")}</strong><Secret value={secret} /></div>}
      <form className="stack" onSubmit={create}>
        <Field label={t("settings.tokenName")}><input className="field" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Jarvis" /></Field>
        <div className="scopes">
          {scopes.map((s) => (
            <label key={s} className={chosen.includes(s) ? "on" : ""}>
              <input type="checkbox" checked={chosen.includes(s)} onChange={(e) => setChosen(e.target.checked ? [...chosen, s] : chosen.filter((x) => x !== s))} />
              <span>{t(`settings.scope.${s}` as never)}<span className="faint mono small-text"> {s}</span></span>
            </label>
          ))}
        </div>
        {error && <div className="error">{error}</div>}
        <button className="btn">{t("settings.newToken")}</button>
      </form>
      <div className="list">
        {tokens.map((tk) => (
          <Row key={tk.id} title={<>{tk.name} <span className="faint mono small-text">{tk.prefix}…</span></>}
            detail={tk.revoked ? t("settings.revoked") : t("settings.lastUsed", { when: tk.last_used ? fmt.relative(tk.last_used) : t("common.never") })}
            right={!tk.revoked && <button className="btn ghost small" onClick={() => api.revokeToken(tk.id).then(load)}>{t("settings.revoke")}</button>} />
        ))}
      </div>
    </div>
  );
}

function UsersPanel({ me }: { me: User }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState("");
  const [link, setLink] = useState<{ name: string; url: string } | null>(null);
  const [error, setError] = useState("");
  const load = () => api.users().then(setUsers);
  useEffect(() => { load(); }, []);
  const invite = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const out = await api.addUser(username, "");
      setLink({ name: out.user.username, url: out.invite_url });
      setUsername("");
      setError("");
      load();
    } catch (err) { setError(errorText(err)); }
  };
  return (
    <div className="stack">
      <p className="muted">{t("settings.usersHint")}</p>
      <form className="ask" onSubmit={invite}>
        <input className="field" value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} placeholder={t("auth.username")} required autoCapitalize="none" />
        <button className="btn">{t("settings.invite")}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {link && <div className="stack"><span>{t("settings.inviteCreated", { name: link.name })}</span><Secret value={link.url} /></div>}
      <div className="list">
        {users.map((u) => (
          <div key={u.id} className="item">
            <span className="avatar small">{(u.name || u.username).slice(0, 1).toUpperCase()}</span>
            <span className="item-main user-row">
            <span className="row-text">
              <span className="row-title">{u.name || u.username} <span className="faint small-text">@{u.username}</span></span>
              <span className="row-detail">{[u.is_admin && t("settings.admin"), u.disabled && t("settings.disabled")].filter(Boolean).join(" · ")}</span>
            </span>
            {u.id !== me.id && (
              <span className="row" style={{ gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
                <button className="btn ghost small" onClick={async () => { const r = await api.reinvite(u.id); setLink({ name: u.username, url: r.invite_url }); }}>{t("settings.resetLink")}</button>
                <button className="btn ghost small" onClick={() => api.patchUser(u.id, { disabled: !u.disabled }).then(load)}>{u.disabled ? t("settings.enable") : t("settings.disable")}</button>
                <button className="btn ghost small danger" aria-label={t("settings.deleteUser")} title={t("settings.deleteUser")} onClick={async () => { if (confirm(`${t("settings.deleteUser")}: ${u.username}. ${t("common.confirmDelete")}`)) { await api.deleteUser(u.id); load(); } }}><Icon name="trash" size={16} /></button>
              </span>
            )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function BankPanel({ system, onDone }: { system: SystemInfo | null; onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [appId, setAppId] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { await api.setBanking(appId, key); onDone(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <form className="stack" onSubmit={save}>
      {system?.banking && <p className="note"><Icon name="check" size={18} /> {t("settings.bankReady")}</p>}
      <p className="muted">{t("settings.bankHint")}</p>
      <p className="small-text">{t("settings.bankSteps")}</p>
      {system?.callback_url && <Secret value={system.callback_url} />}
      <a className="link" href="https://enablebanking.com/cp/applications" target="_blank" rel="noreferrer noopener"><Icon name="globe" size={16} /> enablebanking.com</a>
      <Field label={t("settings.appId")}><input className="field mono" value={appId} onChange={(e) => setAppId(e.target.value)} required /></Field>
      <Field label={t("settings.privateKey")}>
        <textarea className="field mono" rows={5} value={key} onChange={(e) => setKey(e.target.value)} required placeholder="-----BEGIN PRIVATE KEY-----" />
      </Field>
      <input type="file" accept=".pem,.key,.txt" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setKey(await f.text()); }} />
      {error && <div className="error">{error}</div>}
      <button className="btn big-btn" disabled={busy}>{busy ? <Spinner /> : t("common.save")}</button>
    </form>
  );
}

function SystemPanel({ system, onUpdate }: { system: SystemInfo | null; onUpdate: () => void }) {
  const { t } = useI18n();
  if (!system) return <Spinner />;
  const u = system.updates;
  return (
    <div className="stack">
      <div className="list">
        <Row title={t("settings.version")} right={<span className="num">{system.version}</span>} />
        <Row title={u?.update_available ? t("settings.updateAvailable", { version: u.latest?.version ?? "" }) : t("settings.upToDate")}
          right={u?.update_available ? <button className="btn small" onClick={onUpdate}>{t("update.install")}</button> : <Icon name="check" className="up" />} />
        <Row title={t("settings.ai")} right={<span className="muted">{system.ai_model ? t("settings.aiOn", { model: system.ai_model }) : t("settings.aiOff")}</span>} />
      </div>
      {system.inbox && <><h4 className="section-label">{t("settings.inbox")}</h4><p className="muted small-text">{t("settings.inboxHint", { path: system.inbox })}</p></>}
    </div>
  );
}
