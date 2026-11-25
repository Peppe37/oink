import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, type Status } from "./api";
import { I18nProvider, resolveLanguage, type Lang } from "./i18n";
import { Accounts } from "./pages/Accounts";
import { Activity } from "./pages/Activity";
import { Home } from "./pages/Home";
import { Insights } from "./pages/Insights";
import { Invest } from "./pages/Invest";
import { Invite, Login } from "./pages/Login";
import { Settings } from "./pages/Settings";
import { UpdatePrompt, useUpdate } from "./pages/Update";
import { Aurora, Navigation, Toast } from "./ui/bits";
import "./styles.css";

function Shell() {
  const [status, setStatus] = useState<Status | null>(null);
  const [offline, setOffline] = useState(false);
  const [lang, setLang] = useState<Lang>(() => resolveLanguage(null));
  const refresh = useCallback(() => {
    api.status().then((s) => { window.__oinkSSO = s.sso; setStatus(s); setOffline(false); setLang(resolveLanguage(s.user?.language)); }).catch(() => setOffline(true));
  }, []);
  useEffect(refresh, [refresh]);
  const location = useLocation();
  const invite = location.pathname.match(/^\/invite\/([^/]+)/)?.[1];

  return (
    <I18nProvider lang={lang}>
      <Aurora />
      {offline && !status ? (
        <div className="center"><span className="muted">Oink…</span></div>
      ) : !status ? null : invite && !status.user ? (
        <Invite token={invite} onDone={refresh} />
      ) : !status.user ? (
        <Login firstRun={!status.configured} onDone={refresh} onLanguage={setLang} sso={status.sso} passwordLogin={status.password_login} />
      ) : (
        <App status={status} onStatus={refresh} onLanguage={setLang} />
      )}
    </I18nProvider>
  );
}

function App({ status, onStatus, onLanguage }: { status: Status; onStatus: () => void; onLanguage: (l: Lang) => void }) {
  const user = status.user!;
  const location = useLocation();
  const navigate = useNavigate();
  const [toast, setToast] = useState("");
  const [adding, setAdding] = useState(false);
  const notify = useCallback((text: string) => setToast(text), []);
  const { info, pending } = useUpdate(user.is_admin);
  const [showUpdate, setShowUpdate] = useState(false);
  useEffect(() => { if (pending) setShowUpdate(true); }, [pending]);
  const addAccount = () => { navigate("/accounts"); setAdding(true); };

  return (
    <>
      <div className="app">
        <AnimatePresence mode="wait">
          <motion.div key={location.pathname} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }} transition={{ duration: 0.22 }}>
            <Routes location={location}>
              <Route path="/" element={<Home user={user} onAdd={addAccount} />} />
              <Route path="/activity" element={<Activity ai={status.ai_reads_text} notify={notify} />} />
              <Route path="/accounts" element={<Accounts notify={notify} adding={adding} setAdding={setAdding} baseCurrency={user.base_currency} />} />
              <Route path="/invest" element={<Invest notify={notify} currency={user.base_currency} />} />
              <Route path="/insights" element={<Insights ai={status.ai} notify={notify} />} />
              <Route path="/settings" element={<Settings user={user} notify={notify} onLogout={onStatus} onUpdate={() => setShowUpdate(true)}
                onUser={(u) => { onLanguage(resolveLanguage(u.language)); onStatus(); }} />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </motion.div>
        </AnimatePresence>
      </div>
      <Navigation />
      <UpdatePrompt info={info} open={showUpdate} onClose={() => setShowUpdate(false)} />
      <AnimatePresence>{toast && <Toast key={toast} text={toast} onDone={() => setToast("")} />}</AnimatePresence>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <BrowserRouter><Shell /></BrowserRouter>
    </MotionConfig>
  </StrictMode>,
);
