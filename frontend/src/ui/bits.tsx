/* Small shared pieces: the pig, animated money, the dock, toasts, the background. */

import { animate, AnimatePresence, motion, useMotionValue, useTransform } from "framer-motion";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { eur } from "../api";
import { Glass } from "../glass/Glass";

export function Aurora() {
  return (
    <>
      <div className="aurora" aria-hidden><i /><i /><i /><i /></div>
      <div className="grain" aria-hidden />
    </>
  );
}

/** The mascot. Tap it: it hops and says oink. */
export function Pig({ size = 44 }: { size?: number }) {
  const [oink, setOink] = useState(0);
  return (
    <motion.button
      aria-label="Oink"
      onClick={() => setOink((n) => n + 1)}
      style={{ border: 0, background: "none", padding: 0, cursor: "pointer", position: "relative", width: size, height: size }}
      whileHover={{ rotate: -6, scale: 1.06 }}
      animate={oink ? { y: [0, -14, 0, -5, 0], rotate: [0, -8, 6, -3, 0] } : {}}
      transition={{ duration: 0.7, ease: "easeOut" }}
      key={oink}
    >
      <img src="/pig.svg" width={size} height={size} alt="" style={{ borderRadius: size * 0.28, display: "block" }} />
      <AnimatePresence>
        {oink > 0 && (
          <motion.span
            key={oink}
            initial={{ opacity: 0, y: 0, scale: 0.6 }}
            animate={{ opacity: [0, 1, 1, 0], y: -34, scale: 1 }}
            transition={{ duration: 1.1 }}
            style={{ position: "absolute", left: "70%", top: -6, fontWeight: 800, fontSize: 13, color: "var(--pink-2)", whiteSpace: "nowrap", pointerEvents: "none" }}
          >
            oink!
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}

/** Money that counts up to its value (and re-animates when it changes). */
export function Money({ cents, className = "", sign = false, compact = false }: { cents: number | null; className?: string; sign?: boolean; compact?: boolean }) {
  const value = useMotionValue(0);
  const text = useTransform(value, (v) => eur(Math.round(v), { sign, compact }));
  useEffect(() => {
    if (cents === null) return;
    const controls = animate(value, cents, { duration: 1.1, ease: [0.22, 1, 0.36, 1] });
    return () => controls.stop();
  }, [cents, value]);
  if (cents === null) return <span className={className}>—</span>;
  return <motion.span className={`num ${className}`}>{text}</motion.span>;
}

const ICONS: Record<string, ReactNode> = {
  home: <path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  list: <path d="M4 6h16M4 12h16M4 18h10" />,
  wallet: <path d="M3 7a2 2 0 0 1 2-2h13v4M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2Zm14 7h.01" />,
  spark: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />,
  gear: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14.5 3h-4l-.4 2.6a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7 7 0 0 0 2 1.2l.4 2.6h4l.4-2.6a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2Z" />,
};

const TABS = [
  { to: "/", label: "Home", icon: "home" },
  { to: "/movimenti", label: "Movimenti", icon: "list" },
  { to: "/conti", label: "Conti", icon: "wallet" },
  { to: "/resoconti", label: "Resoconti", icon: "spark" },
  { to: "/impostazioni", label: "Altro", icon: "gear" },
];

export function Dock() {
  const { pathname } = useLocation();
  return (
    <Glass refract variant="pill" className="dock" strength={44} initial={{ y: 80, x: "-50%", opacity: 0 }} animate={{ y: 0, x: "-50%", opacity: 1 }} transition={{ type: "spring", stiffness: 260, damping: 26, delay: 0.2 }}>
      {TABS.map((tab) => {
        const active = tab.to === "/" ? pathname === "/" : pathname.startsWith(tab.to);
        return (
          <NavLink key={tab.to} to={tab.to} className={active ? "active" : ""}>
            {active && <motion.span layoutId="dock-bubble" className="bubble" transition={{ type: "spring", stiffness: 420, damping: 34 }} />}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{ICONS[tab.icon]}</svg>
            {tab.label}
          </NavLink>
        );
      })}
    </Glass>
  );
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3200);
    return () => clearTimeout(t);
  }, [text, onDone]);
  return (
    <Glass variant="pill" className="toast" initial={{ y: -40, x: "-50%", opacity: 0 }} animate={{ y: 0, x: "-50%", opacity: 1 }} exit={{ y: -40, x: "-50%", opacity: 0 }}>
      {text}
    </Glass>
  );
}

export const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } },
};
export const rise = {
  hidden: { opacity: 0, y: 24, scale: 0.98, filter: "blur(6px)" },
  show: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)", transition: { type: "spring", stiffness: 220, damping: 24 } },
};
