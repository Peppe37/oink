/* «A new version is here»: the admin sees it once per version. Install and restart asks the
   updater sidecar to pull the new image; the page waits for the new version and reloads. */

import { useEffect, useState } from "react";
import { Markdown as ReactMarkdown } from "../ui/Markdown";
import { api, type UpdateInfo } from "../api";
import { useErrorText, useI18n } from "../i18n";
import { Sheet, Spinner } from "../ui/bits";
import { Icon } from "../ui/icons";

const SEEN = "oink.update.dismissed";

export function useUpdate(admin: boolean) {
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  useEffect(() => {
    if (!admin) return;
    const check = () => api.system().then((s) => setInfo(s.updates ?? null)).catch(() => undefined);
    check();
    const id = setInterval(check, 6 * 3600 * 1000);
    return () => clearInterval(id);
  }, [admin]);
  const dismissed = (() => { try { return localStorage.getItem(SEEN); } catch { return null; } })();
  const pending = !!info?.update_available && info.latest?.version !== dismissed;
  return { info, pending };
}

export function UpdatePrompt({ info, open, onClose }: { info: UpdateInfo | null; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const errorText = useErrorText();
  const [state, setState] = useState<"idle" | "installing" | "failed">("idle");
  const [error, setError] = useState("");
  const target = info?.latest?.version ?? "";

  const later = () => {
    try { localStorage.setItem(SEEN, target); } catch { /* private mode */ }
    onClose();
  };
  const install = async () => {
    setState("installing");
    try {
      await api.installUpdate();
    } catch (e) {
      setError(errorText(e));
      setState("failed");
      return;
    }
    const started = Date.now();
    const poll = async () => {
      if (Date.now() - started > 8 * 60 * 1000) { setState("failed"); setError(t("update.failed")); return; }
      try {
        const health = await api.health();
        if (health.version === target) { window.location.reload(); return; }
      } catch { /* restarting */ }
      setTimeout(poll, 2500);
    };
    setTimeout(poll, 4000);
  };

  return (
    <Sheet open={open && !!info} onClose={state === "installing" ? () => undefined : later} title={t("update.title", { version: target })}>
      <div className="stack">
        <div className="update-hero"><span className="update-badge"><Icon name="download" size={30} /></span><p className="muted">{t("update.body")}</p></div>
        {info?.latest?.notes && <div className="md notes"><ReactMarkdown>{info.latest.notes}</ReactMarkdown></div>}
        {state === "installing" && <p className="row" style={{ gap: 10 }}><Spinner /> {t("update.installing")}</p>}
        {error && <div className="error">{error}</div>}
        {!info?.can_install && (
          <div className="stack">
            <span className="small-text">{t("update.manual")}</span>
            <code className="secret">docker compose pull && docker compose up -d</code>
          </div>
        )}
        <div className="row between">
          <button className="btn ghost" onClick={later} disabled={state === "installing"}>{t("common.later")}</button>
          {info?.can_install && <button className="btn" onClick={install} disabled={state === "installing"}>{t("update.install")}</button>}
        </div>
      </div>
    </Sheet>
  );
}
