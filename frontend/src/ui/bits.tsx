/* Shared pieces: background, mascot, animated money, navigation (tab bar on phones,
   sidebar on large screens), sheets, toasts, list rows. */

import type { Prefs } from "../api";
import { animate, AnimatePresence, motion, useDragControls, useMotionValue, useSpring, useTransform, type MotionValue, type PanInfo } from "framer-motion";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import { Glass } from "../glass/Glass";
import { useI18n, type Key } from "../i18n";
import { Icon } from "./icons";

export function useWide(min = 900) {
  const query = `(min-width: ${min}px)`;
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return wide;
}

/** The page background: the soft moving colours, or the person's own picture (blurred or not). */
export function Aurora({ prefs }: { prefs?: Prefs }) {
  const picture = prefs?.background === "image";
  return (
    <>
      {picture ? (
        <div className={`bg-image ${prefs?.blur ? "blur" : ""}`} aria-hidden
          style={{ backgroundImage: `url(/api/me/background?v=${prefs?.bg_version ?? 0})` }} />
      ) : (
        <div className="aurora" aria-hidden><i /><i /><i /><i /></div>
      )}
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
      className="pig"
      onClick={() => setOink((n) => n + 1)}
      style={{ width: size, height: size }}
      whileHover={{ rotate: -6, scale: 1.06 }}
      animate={oink ? { y: [0, -14, 0, -5, 0], rotate: [0, -8, 6, -3, 0] } : {}}
      transition={{ duration: 0.7, ease: "easeOut" }}
      key={oink}
    >
      <img src="/pig.svg" width={size} height={size} alt="" style={{ borderRadius: size * 0.28, display: "block" }} />
      <AnimatePresence>
        {oink > 0 && (
          <motion.span key={oink} className="oink" initial={{ opacity: 0, y: 0, scale: 0.6 }}
            animate={{ opacity: [0, 1, 1, 0], y: -34, scale: 1 }} transition={{ duration: 1.1 }}>
            oink!
          </motion.span>
        )}
      </AnimatePresence>
    </motion.button>
  );
}

/** Money that counts up to its value (and re-animates when it changes). */
export function Money({ value, currency, className = "", sign = false, compact = false }: { value: number | string | null; currency: string; className?: string; sign?: boolean; compact?: boolean }) {
  const { fmt } = useI18n();
  const target = value === null || value === "" ? null : Number(value);
  const mv = useMotionValue(0);
  const text = useTransform(mv, (v) => fmt.money(v, currency, { sign, compact }));
  useEffect(() => {
    if (target === null) return;
    const controls = animate(mv, target, { duration: 1.1, ease: [0.22, 1, 0.36, 1] });
    return () => controls.stop();
  }, [target, mv]);
  if (target === null) return <span className={className}>—</span>;
  return <motion.span className={`num ${className}`}>{text}</motion.span>;
}

const TABS: { to: string; label: Key; icon: string; colors: [string, string] }[] = [
  { to: "/", label: "nav.home", icon: "home", colors: ["#ff9ccc", "#e0468f"] },
  { to: "/activity", label: "nav.movements", icon: "activity", colors: ["#8fd3ff", "#2f7fe8"] },
  { to: "/accounts", label: "nav.accounts", icon: "wallet", colors: ["#b9a6ff", "#6a4fe6"] },
  { to: "/invest", label: "nav.invest", icon: "investments", colors: ["#7ff0c9", "#0f9e7c"] },
  { to: "/documents", label: "nav.documents", icon: "documents", colors: ["#9fe6ff", "#2aa7c9"] },
  { to: "/insights", label: "nav.reports", icon: "insights", colors: ["#ffd27a", "#f08a1c"] },
  { to: "/settings", label: "nav.settings", icon: "settings", colors: ["#c9c6d6", "#6f6a85"] },
];

/** The dock, like a Mac's: glass shelf at the bottom, icons that swell as the pointer (or a
 *  finger sliding along it) gets close, bounce when opened, a dot under the open section. */
export function Navigation() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const wide = useWide(700);
  const pointer = useMotionValue(Infinity);
  const [bounce, setBounce] = useState<string | null>(null);
  const base = wide ? 50 : 40;
  const peak = wide ? 78 : 56;
  const active = (to: string) => (to === "/" ? pathname === "/" : pathname.startsWith(to));
  const open = (to: string) => {
    setBounce(to);
    setTimeout(() => setBounce(null), 700);
    navigate(to);
  };
  return (
    <nav className="dock-wrap" aria-label="Oink">
      <Glass refract strength={30} radius={24} className="dock"
        onPointerMove={(e) => pointer.set(e.clientX)}
        onPointerLeave={() => pointer.set(Infinity)}
        onTouchMove={(e) => pointer.set(e.touches[0].clientX)}
        onTouchEnd={() => pointer.set(Infinity)}
        initial={{ y: 120, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ type: "spring", stiffness: 220, damping: 24, delay: 0.1 }}>
        {TABS.map((tab) => (
          <DockIcon key={tab.to} pointer={pointer} base={base} peak={peak} label={t(tab.label)} icon={tab.icon}
            colors={tab.colors} active={active(tab.to)} bouncing={bounce === tab.to} onOpen={() => open(tab.to)} />
        ))}
      </Glass>
    </nav>
  );
}

function DockIcon({ pointer, base, peak, label, icon, colors, active, bouncing, onOpen }: {
  pointer: MotionValue<number>; base: number; peak: number; label: string; icon: string; colors: [string, string];
  active: boolean; bouncing: boolean; onOpen: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const distance = useTransform(pointer, (x) => {
    const box = ref.current?.getBoundingClientRect();
    return box ? x - (box.left + box.width / 2) : Infinity;
  });
  const target = useTransform(distance, [-base * 2.6, 0, base * 2.6], [base, peak, base]);
  const size = useSpring(target, { stiffness: 340, damping: 22, mass: 0.35 });
  return (
    <motion.button ref={ref} className="dock-item" aria-label={label} aria-current={active ? "page" : undefined}
      style={{ width: size, height: base }} onClick={onOpen}
      animate={bouncing ? { y: [0, -22, 0, -8, 0] } : { y: 0 }} transition={{ duration: 0.65, ease: "easeOut" }}>
      <span className="dock-tile" style={{ background: `linear-gradient(150deg, ${colors[0]}, ${colors[1]})` }}>
        <Icon name={icon} size={22} stroke={1.9} style={{ width: "50%", height: "50%" }} />
      </span>
      <motion.i className="dock-dot" animate={{ opacity: active ? 1 : 0, scale: active ? 1 : 0.4 }} />
    </motion.button>
  );
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <motion.header variants={rise} className="page-header">
      <div className="ellipsis">
        {subtitle && <div className="muted subtitle">{subtitle}</div>}
        <h1 className="title">{title}</h1>
      </div>
      {action}
    </motion.header>
  );
}

/** A sheet: slides up from the bottom on phones (drag down to close), a centred panel on
 *  large screens. Rendered in a portal so it covers the tab bar. */
export function Sheet({ open, onClose, title, children, wide = false }: { open: boolean; onClose: () => void; title?: string; children: ReactNode; wide?: boolean }) {
  const large = useWide();
  const drag = useDragControls();
  const { t } = useI18n();
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    document.body.classList.add("locked");
    return () => { document.removeEventListener("keydown", key); document.body.classList.remove("locked"); };
  }, [open, onClose]);
  const onDragEnd = (_: unknown, info: PanInfo) => { if (info.offset.y > 120 || info.velocity.y > 600) onClose(); };
  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div className="sheet-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={onClose}>
          <Glass frost={28}
            className={`sheet ${large ? "panel" : ""} ${wide ? "wide" : ""}`}
            role="dialog" aria-modal="true" aria-label={title}
            onClick={(e) => e.stopPropagation()}
            initial={large ? { opacity: 0, scale: 0.94, y: 20 } : { y: "100%" }}
            animate={large ? { opacity: 1, scale: 1, y: 0 } : { y: 0 }}
            exit={large ? { opacity: 0, scale: 0.96, y: 10 } : { y: "100%" }}
            transition={{ type: "spring", stiffness: 380, damping: 36 }}
            drag={large ? false : "y"} dragControls={drag} dragListener={false}
            dragConstraints={{ top: 0, bottom: 0 }} dragElastic={{ top: 0, bottom: 0.6 }} onDragEnd={onDragEnd}
          >
            {!large && <div className="grabber" onPointerDown={(e) => drag.start(e)}><i /></div>}
            <div className="sheet-head" onPointerDown={(e) => !large && drag.start(e)}>
              <h2>{title}</h2>
              <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}><Icon name="close" size={20} /></button>
            </div>
            <div className="sheet-body">{children}</div>
          </Glass>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

export function Toast({ text, onDone }: { text: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 3600);
    return () => clearTimeout(t);
  }, [text, onDone]);
  return createPortal(
    <Glass variant="pill" frost={16} className="toast" role="status" initial={{ y: -40, x: "-50%", opacity: 0 }} animate={{ y: 0, x: "-50%", opacity: 1 }} exit={{ y: -40, x: "-50%", opacity: 0 }}>
      {text}
    </Glass>,
    document.body,
  );
}

export function Row({ icon, title, detail, right, onClick, chevron = false }: { icon?: ReactNode; title: ReactNode; detail?: ReactNode; right?: ReactNode; onClick?: () => void; chevron?: boolean }) {
  const content = (
    <>
      {icon}
      <span className="item-main">
        <span className="row-text">
          <span className="row-title ellipsis">{title}</span>
          {detail && <span className="row-detail ellipsis">{detail}</span>}
        </span>
        {right && <span className="row-right">{right}</span>}
        {chevron && <Icon name="chevron" size={18} className="faint" />}
      </span>
    </>
  );
  return onClick ? <button className="item press-row" onClick={onClick}>{content}</button> : <div className="item">{content}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="labeled">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function Empty({ icon, title, hint, action }: { icon: string; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-icon"><Icon name={icon} size={30} /></span>
      <strong>{title}</strong>
      {hint && <span className="muted">{hint}</span>}
      {action}
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden />;
}

export const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.04 } },
};
export const rise = {
  hidden: { opacity: 0, y: 22, scale: 0.98, filter: "blur(6px)" },
  show: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)", transition: { type: "spring", stiffness: 220, damping: 24 } },
};
