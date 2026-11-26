/* Sign in, sign up (when the admin allows it) and accepting an invite. */

import { motion } from "framer-motion";
import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { Glass } from "../glass/Glass";
import { LANGUAGES, deviceLanguage, useErrorText, useI18n, type Lang } from "../i18n";
import { Field, Pig, Spinner } from "../ui/bits";
import { Icon } from "../ui/icons";

const COMMON = ["EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN", "CZK"];

function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  const { t } = useI18n();
  return (
    <div className="center">
      <Glass refract strength={46} className="card auth" initial={{ opacity: 0, y: 30, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} transition={{ type: "spring", stiffness: 200, damping: 22 }}>
        <div className="auth-head">
          <Pig size={64} />
          <h1 className="title">{title}</h1>
          <p className="muted">{hint ?? t("app.tagline")}</p>
        </div>
        {children}
      </Glass>
    </div>
  );
}

export function Login({ canSignUp, onDone, onLanguage, sso, passwordLogin }: { canSignUp: boolean; onDone: () => void; onLanguage: (l: Lang) => void; sso: string | null; passwordLogin: boolean }) {
  const { t, lang } = useI18n();
  const errorText = useErrorText();
  const [mode, setMode] = useState<"in" | "up">("in");
  const firstRun = mode === "up";
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [currency, setCurrency] = useState("EUR");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (firstRun) await api.signup({ username, password, name, language: lang === deviceLanguage() ? "" : lang, base_currency: currency });
      else await api.login(username, password);
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={firstRun ? t("auth.signupTitle") : "Oink"} hint={firstRun ? t("auth.signupHint") : undefined}>
      {canSignUp && passwordLogin && (
        <div className="segmented auth-tabs" role="tablist">
          {(["in", "up"] as const).map((m) => (
            <button key={m} type="button" role="tab" aria-selected={mode === m} className={mode === m ? "on" : ""} onClick={() => { setMode(m); setError(""); }}>
              {m === "in" ? t("auth.signIn") : t("auth.signUp")}
            </button>
          ))}
        </div>
      )}
      {sso && (
        <div className="stack" style={{ marginBottom: 16 }}>
          <a className="btn big-btn" href="/api/auth/oidc/start"><Icon name="key" size={18} /> {t("auth.sso", { name: sso })}</a>
          {passwordLogin && <div className="divider"><span>{t("auth.or")}</span></div>}
        </div>
      )}
      {passwordLogin && <form onSubmit={submit} className="stack">
        {firstRun && (
          <Field label={t("settings.name")}>
            <input className="field" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        )}
        <Field label={t("auth.username")} hint={firstRun ? t("auth.usernameHint") : undefined}>
          <input className="field" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </Field>
        <Field label={t("auth.password")} hint={firstRun ? t("auth.passwordHint") : undefined}>
          <input className="field" type="password" autoComplete={firstRun ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        {firstRun && (
          <div className="two">
            <Field label={t("auth.language")}>
              <select className="field" value={lang} onChange={(e) => onLanguage(e.target.value as Lang)}>
                {Object.entries(LANGUAGES).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
              </select>
            </Field>
            <Field label={t("auth.baseCurrency")}>
              <select className="field" value={currency} onChange={(e) => setCurrency(e.target.value)}>
                {COMMON.map((c) => <option key={c}>{c}</option>)}
              </select>
            </Field>
          </div>
        )}
        {error && <motion.div className="error" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>{error}</motion.div>}
        <button className="btn big-btn" disabled={busy}>{busy ? <Spinner /> : firstRun ? t("auth.signUp") : t("auth.signIn")}</button>
      </form>}
      {new URLSearchParams(window.location.search).get("sso") === "error" && <div className="error" style={{ marginTop: 12 }}>{t("auth.ssoError")}</div>}
    </Card>
  );
}

export function Invite({ token, onDone }: { token: string; onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [who, setWho] = useState<{ username: string; name: string } | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.invite(token).then(setWho).catch((e) => setError(errorText(e))); }, [token, errorText]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.acceptInvite(token, password);
      window.history.replaceState(null, "", "/");
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={who ? t("auth.inviteTitle", { name: who.name || who.username }) : "Oink"} hint={who ? t("auth.inviteHint") : undefined}>
      {who ? (
        <form onSubmit={submit} className="stack">
          <Field label={t("auth.username")}><input className="field" value={who.username} readOnly autoComplete="username" /></Field>
          <Field label={t("auth.password")} hint={t("auth.passwordHint")}>
            <input className="field" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required autoFocus />
          </Field>
          {error && <div className="error">{error}</div>}
          <button className="btn big-btn" disabled={busy}>{busy ? <Spinner /> : t("auth.start")}</button>
        </form>
      ) : (
        <div className="error">{error || t("common.loading")}</div>
      )}
    </Card>
  );
}

/* A password given by the admin (or the admin's default one) is changed before anything else. */
export function MustChangePassword({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (next !== again) { setError(t("auth.passwordsDiffer")); return; }
    setBusy(true);
    try { await api.password(current, next); onDone(); } catch (err) { setError(errorText(err)); } finally { setBusy(false); }
  };
  return (
    <Card title={t("auth.changeTitle")} hint={t("auth.changeHint")}>
      <form onSubmit={submit} className="stack">
        <input type="text" autoComplete="username" hidden readOnly />
        <Field label={t("settings.currentPassword")}><input className="field" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required autoFocus /></Field>
        <Field label={t("settings.newPassword")} hint={t("auth.passwordHint")}><input className="field" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={10} /></Field>
        <Field label={t("auth.repeatPassword")}><input className="field" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required minLength={10} /></Field>
        {error && <div className="error">{error}</div>}
        <button className="btn big-btn" disabled={busy}>{busy ? <Spinner /> : t("settings.changePassword")}</button>
        <button type="button" className="btn ghost" onClick={async () => { await api.logout(); onDone(); }}>{t("auth.signOut")}</button>
      </form>
    </Card>
  );
}
