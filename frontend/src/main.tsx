import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, type Status } from "./api";
import { Accounts } from "./pages/Accounts";
import { Home } from "./pages/Home";
import { Login } from "./pages/Login";
import { Movements } from "./pages/Movements";
import { Reports } from "./pages/Reports";
import { Settings } from "./pages/Settings";
import { Aurora, Dock, Toast } from "./ui/bits";
import "./styles.css";

function Shell() {
  const [status, setStatus] = useState<Status | null>(null);
  const [toast, setToast] = useState("");
  const location = useLocation();
  const notify = useCallback((text: string) => setToast(text), []);
  const refresh = useCallback(() => { api.status().then(setStatus).catch(() => setStatus(null)); }, []);
  useEffect(refresh, [refresh]);

  if (!status) return <Aurora />;
  if (!status.logged_in) return (<><Aurora /><Login firstRun={!status.configured} onDone={refresh} /></>);

  return (
    <>
      <Aurora />
      <div className="app">
        <AnimatePresence mode="wait">
          <motion.div key={location.pathname} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }} transition={{ duration: 0.25 }}>
            <Routes location={location}>
              <Route path="/" element={<Home />} />
              <Route path="/movimenti" element={<Movements notify={notify} />} />
              <Route path="/conti" element={<Accounts notify={notify} />} />
              <Route path="/resoconti" element={<Reports ai={status.ai} notify={notify} />} />
              <Route path="/impostazioni" element={<Settings status={status} notify={notify} onLogout={refresh} />} />
              <Route path="/settings" element={<Settings status={status} notify={notify} onLogout={refresh} />} />
              <Route path="*" element={<Home />} />
            </Routes>
          </motion.div>
        </AnimatePresence>
      </div>
      <Dock />
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
