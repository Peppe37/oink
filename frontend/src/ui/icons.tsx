/* Oink's own icon set: 24×24, 1.75 stroke, rounded joins, drawn for this app. Categories sit
   on a glossy tile with their own colour, so a list of movements reads at a glance. */

import type { CSSProperties, ReactNode } from "react";

const P: Record<string, ReactNode> = {
  // navigation and actions
  home: <><path d="M4 10.5 12 4l8 6.5" /><path d="M6 9v10.2c0 .4.4.8.8.8H10v-5.5h4V20h3.2c.4 0 .8-.4.8-.8V9" /></>,
  activity: <><path d="M4 7h11" /><path d="M4 12h16" /><path d="M4 17h8" /><circle cx="19" cy="7" r="1.6" /><circle cx="16" cy="17" r="1.6" /></>,
  wallet: <><rect x="3.5" y="6" width="17" height="13" rx="3" /><path d="M3.5 10h17" /><path d="M7 6V5.2C7 4.5 7.6 4 8.3 4h9.4c.7 0 1.3.5 1.3 1.2V6" /><circle cx="16.5" cy="14.5" r="1.2" /></>,
  insights: <><path d="M12 3.5 13.6 8l4.4 1.6-4.4 1.6L12 15.7l-1.6-4.5L6 9.6 10.4 8z" /><path d="M18.5 15.5 19.2 17.3 21 18l-1.8.7-.7 1.8-.7-1.8L16 18l1.8-.7z" /><path d="M5.5 15.5l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5z" /></>,
  settings: <><path d="M4 7h9" /><path d="M17 7h3" /><circle cx="15" cy="7" r="2" /><path d="M4 17h3" /><path d="M11 17h9" /><circle cx="9" cy="17" r="2" /></>,
  plus: <><path d="M12 5v14" /><path d="M5 12h14" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  upload: <><path d="M12 15V4.5" /><path d="m7.5 9 4.5-4.5L16.5 9" /><path d="M4.5 15v3.2c0 1 .8 1.8 1.8 1.8h11.4c1 0 1.8-.8 1.8-1.8V15" /></>,
  sync: <><path d="M19.5 11A7.5 7.5 0 0 0 6.3 6.7L4.5 8.5" /><path d="M4.5 4.5v4h4" /><path d="M4.5 13a7.5 7.5 0 0 0 13.2 4.3l1.8-1.8" /><path d="M19.5 19.5v-4h-4" /></>,
  close: <><path d="m6 6 12 12" /><path d="M18 6 6 18" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  back: <path d="m15 5-7 7 7 7" />,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  trash: <><path d="M4.5 7h15" /><path d="M9.5 7V5.2c0-.7.5-1.2 1.2-1.2h2.6c.7 0 1.2.5 1.2 1.2V7" /><path d="M6.5 7l.9 11.6c.1 1 .9 1.4 1.8 1.4h5.6c.9 0 1.7-.4 1.8-1.4L17.5 7" /></>,
  key: <><circle cx="8" cy="15" r="3.5" /><path d="m10.5 12.5 8-8" /><path d="m16 7 2.5 2.5" /><path d="m13.5 9.5 2 2" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  users: <><circle cx="9" cy="9" r="3" /><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6" /><path d="M15.5 6.2a3 3 0 0 1 0 5.6" /><path d="M17.5 14.6c1.6.6 2.7 2 3 4.4" /></>,
  shield: <><path d="M12 3.5 5 6.2v5.3c0 4.2 2.9 7.6 7 9 4.1-1.4 7-4.8 7-9V6.2z" /><path d="m9 12 2.2 2.2L15.5 10" /></>,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17" /><path d="M12 3.5c2.3 2.3 3.4 5.2 3.4 8.5s-1.1 6.2-3.4 8.5c-2.3-2.3-3.4-5.2-3.4-8.5S9.7 5.8 12 3.5z" /></>,
  download: <><path d="M12 4.5V15" /><path d="m7.5 10.5 4.5 4.5 4.5-4.5" /><path d="M4.5 15v3.2c0 1 .8 1.8 1.8 1.8h11.4c1 0 1.8-.8 1.8-1.8V15" /></>,
  inbox: <><path d="M4 13.5 6.3 5.6c.2-.7.8-1.1 1.5-1.1h8.4c.7 0 1.3.4 1.5 1.1l2.3 7.9" /><path d="M4 13.5V18c0 1 .8 1.5 1.5 1.5h13c.7 0 1.5-.5 1.5-1.5v-4.5h-4.5l-1.2 2h-4.6l-1.2-2z" /></>,
  sparkle: <path d="M12 4l1.8 5.2L19 11l-5.2 1.8L12 18l-1.8-5.2L5 11l5.2-1.8z" />,
  logout: <><path d="M14 4.5h3.7c1 0 1.8.8 1.8 1.8v11.4c0 1-.8 1.8-1.8 1.8H14" /><path d="M10 8 6 12l4 4" /><path d="M6 12h10" /></>,
  arrowUp: <><path d="M12 19V5" /><path d="m6 11 6-6 6 6" /></>,
  arrowDown: <><path d="M12 5v14" /><path d="m6 13 6 6 6-6" /></>,
  // categories
  groceries: <><path d="M3.5 9.5h17l-1.7 8.6c-.2.8-.9 1.4-1.7 1.4H6.9c-.8 0-1.5-.6-1.7-1.4z" /><path d="m8 9.5 3-5.5" /><path d="m16 9.5-3-5.5" /><path d="M9 13v3" /><path d="M12 13v3" /><path d="M15 13v3" /></>,
  restaurants: <><path d="M7 3.5v7" /><path d="M4.5 3.5V8c0 1.4 1.1 2.5 2.5 2.5S9.5 9.4 9.5 8V3.5" /><path d="M7 10.5v10" /><path d="M17 20.5v-17c-2.2 1-3.5 3.6-3.5 6.5 0 2 1.3 3 3.5 3" /></>,
  bills: <path d="M13 3 5.5 13.2H12l-1 7.8 7.5-10.2H12z" />,
  transport: <><rect x="6" y="3.5" width="12" height="13" rx="3" /><path d="M6 10.5h12" /><circle cx="9" cy="13.5" r=".9" /><circle cx="15" cy="13.5" r=".9" /><path d="m8 16.5-2 4" /><path d="m16 16.5 2 4" /><path d="M9.5 6.5h5" /></>,
  car: <><path d="M4 15.5v-3.2l1.8-4.6c.3-.7.9-1.2 1.7-1.2h9c.8 0 1.4.5 1.7 1.2l1.8 4.6v3.2" /><path d="M3.5 12.3h17" /><rect x="3.5" y="12.3" width="17" height="4.7" rx="1.5" /><path d="M6 17v2" /><path d="M18 17v2" /><circle cx="7.5" cy="14.6" r=".8" /><circle cx="16.5" cy="14.6" r=".8" /></>,
  health: <><path d="M12 20s-7.5-4.4-7.5-10A4.2 4.2 0 0 1 12 7.3 4.2 4.2 0 0 1 19.5 10c0 5.6-7.5 10-7.5 10z" /><path d="M7.5 12.5h2.3l1.3-2.3 1.8 4 1.2-1.7h2.4" /></>,
  leisure: <><path d="M4 8.5c0-.8.7-1.5 1.5-1.5h13c.8 0 1.5.7 1.5 1.5v1.8a1.8 1.8 0 0 0 0 3.4v1.8c0 .8-.7 1.5-1.5 1.5h-13c-.8 0-1.5-.7-1.5-1.5v-1.8a1.8 1.8 0 0 0 0-3.4z" /><path d="M14.5 7v10" strokeDasharray="1.6 1.8" /></>,
  travel: <path d="M10.5 13.5 4 11.2l1.2-1.6 7 .7 4.4-4.7c.8-.8 2.2-.9 2.8-.3.6.6.5 2-.3 2.8l-4.7 4.4.7 7-1.6 1.2-2.3-6.5-3.4 3.2.3 2-1.2 1-1.4-3-3-1.4 1-1.2 2 .3z" />,
  subscriptions: <><path d="M19 9.5A7.3 7.3 0 0 0 5.6 8" /><path d="M5 4.5V8.2h3.7" /><path d="M5 14.5A7.3 7.3 0 0 0 18.4 16" /><path d="M19 19.5v-3.7h-3.7" /><path d="M10.5 10v4l3.3-2z" /></>,
  shopping: <><path d="M5.5 8h13l-.9 11c-.1.8-.7 1.5-1.5 1.5H7.9c-.8 0-1.4-.7-1.5-1.5z" /><path d="M9 10.5V7a3 3 0 0 1 6 0v3.5" /></>,
  education: <><path d="m12 5 9 4.5-9 4.5-9-4.5z" /><path d="M6.5 11.3v4.2c0 1.3 2.5 3 5.5 3s5.5-1.7 5.5-3v-4.2" /><path d="M21 9.5v5" /></>,
  gifts: <><rect x="4" y="9" width="16" height="4" rx="1" /><path d="M5.5 13v6c0 .6.4 1 1 1h11c.6 0 1-.4 1-1v-6" /><path d="M12 9v11" /><path d="M12 9C10.5 6 7 5.2 7 7.3 7 9 12 9 12 9zm0 0c1.5-3 5-3.8 5-1.7C17 9 12 9 12 9z" /></>,
  taxes: <><path d="M3.5 9 12 4l8.5 5" /><path d="M5 9.5h14" /><path d="M6.5 9.5v7.5" /><path d="M10 9.5v7.5" /><path d="M14 9.5v7.5" /><path d="M17.5 9.5v7.5" /><path d="M4 20h16" /><path d="M5 17h14" /></>,
  fees: <><path d="M6 3.5h12v17l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4-2 1.4z" /><path d="m9.5 14.5 5-5" /><circle cx="9.8" cy="9.8" r=".9" /><circle cx="14.2" cy="14.2" r=".9" /></>,
  salary: <><rect x="3.5" y="7.5" width="17" height="12" rx="2.2" /><path d="M9 7.5V6c0-.8.7-1.5 1.5-1.5h3c.8 0 1.5.7 1.5 1.5v1.5" /><path d="M3.5 12.5h17" /><path d="M10.5 12.5v1.6h3v-1.6" /></>,
  income: <><path d="M12 3.5V13" /><path d="m8 9 4 4 4-4" /><path d="M4 13.5V18c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2v-4.5" /></>,
  transfers: <><path d="M5 8.5h13" /><path d="m14.5 5 3.5 3.5-3.5 3.5" /><path d="M19 15.5H6" /><path d="m9.5 12-3.5 3.5L9.5 19" /></>,
  savings: <><path d="M5.5 11.5C5.5 8 8.4 6 12 6c1.3 0 2.5.3 3.5.8l2.3-1.3-.5 2.7c.8.7 1.4 1.6 1.7 2.6h1.5v3.4h-1.6c-.4.9-1 1.7-1.9 2.3V19h-2.5v-1.6c-.8.2-1.6.3-2.5.3s-1.7-.1-2.5-.3V19H7v-2.8c-1-1-1.5-2.4-1.5-4.7z" /><circle cx="15.6" cy="10.4" r=".8" /><path d="M10 8.6h3" /><path d="M5.5 11.5c-1 0-2-.6-2-1.8" /></>,
  investments: <><path d="M4 19.5h16" /><path d="m5 15 4.5-4.5 3.5 3 6-6.5" /><path d="M15 7h4v4" /></>,
  crypto: <><path d="m12 3 7.5 4.3v9.4L12 21l-7.5-4.3V7.3z" /><path d="M10 8v8" /><path d="M10 8h3.2a2 2 0 0 1 0 4H10h3.7a2 2 0 0 1 0 4H10" /><path d="M11.5 6.5V8" /><path d="M11.5 16v1.5" /></>,
  other: <><circle cx="6.5" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="17.5" cy="12" r="1.4" /></>,
  none: <><circle cx="12" cy="12" r="8.5" strokeDasharray="2.4 2.2" /><path d="M9.7 9.6a2.4 2.4 0 1 1 3.3 2.2c-.6.3-1 .8-1 1.5v.4" /><circle cx="12" cy="16.6" r=".5" /></>,
  // account kinds
  checking: <><path d="M3.5 9 12 4.5 20.5 9" /><path d="M5 9.5h14" /><path d="M6.5 10v7" /><path d="M10.5 10v7" /><path d="M13.5 10v7" /><path d="M17.5 10v7" /><path d="M4 19.5h16" /></>,
  card: <><rect x="3" y="5.5" width="18" height="13" rx="2.5" /><path d="M3 10h18" /><path d="M6.5 15h3.5" /></>,
  cash: <><rect x="3" y="6.5" width="18" height="11" rx="2" /><circle cx="12" cy="12" r="2.6" /><path d="M6 9.5v.01" /><path d="M18 14.5v.01" /></>,
  investment: <><path d="M4 19.5h16" /><rect x="5.5" y="12" width="3" height="5.5" rx=".8" /><rect x="10.5" y="8.5" width="3" height="9" rx=".8" /><rect x="15.5" y="5" width="3" height="12.5" rx=".8" /></>,
  bank: <><path d="M3.5 9 12 4.5 20.5 9" /><path d="M5 9.5h14" /><path d="M6.5 10v7" /><path d="M10.5 10v7" /><path d="M13.5 10v7" /><path d="M17.5 10v7" /><path d="M4 19.5h16" /></>,
  exchange: <><path d="M7 4.5v15" /><path d="m4 16.5 3 3 3-3" /><path d="M17 19.5v-15" /><path d="m14 7.5 3-3 3 3" /></>,
  fintech: <><circle cx="12" cy="12" r="8.5" /><path d="M7.5 9.5h9" /><path d="M7.5 14.5h9" /><path d="M12 6.5v11" /></>,
  manual: <><path d="M6 3.5h8.5L19 8v11.2c0 .7-.6 1.3-1.3 1.3H6.3c-.7 0-1.3-.6-1.3-1.3V4.8c0-.7.6-1.3 1.3-1.3z" /><path d="M14 3.5V8.5h5" /><path d="M8.5 13h7" /><path d="M8.5 16.5h4.5" /></>,
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 22, stroke = 1.75, style, className }: { name: string; size?: number; stroke?: number; style?: CSSProperties; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden className={className} style={{ flex: "none", ...style }}>
      {P[name] ?? P.other}
    </svg>
  );
}

/** Colour of each category: a hue pair for the tile's gradient. */
export const CATEGORY_COLORS: Record<string, [string, string]> = {
  groceries: ["#43e5b5", "#0fa37f"], restaurants: ["#ffb26b", "#f0703c"], home: ["#a08bff", "#6a4fe6"],
  bills: ["#ffd76a", "#f2a71b"], transport: ["#6cc9ff", "#2f86e8"], car: ["#7ea5ff", "#4a63e0"],
  health: ["#ff8aa0", "#e8456a"], leisure: ["#ff86c8", "#d9479a"], travel: ["#5fe1ff", "#1aa3d6"],
  subscriptions: ["#cf95ff", "#9550e8"], shopping: ["#ffa3cf", "#ec5ea4"], education: ["#a8e66b", "#5fae24"],
  gifts: ["#ff9b9b", "#e85a5a"], taxes: ["#c4c4d4", "#7f7f97"], fees: ["#b6b2c6", "#7a7590"],
  salary: ["#5ef0aa", "#16b06a"], income: ["#5ef0aa", "#16b06a"], transfers: ["#a9b0c8", "#6c7491"],
  savings: ["#ffb8d6", "#f2709f"], investments: ["#6ee7d0", "#13a593"], crypto: ["#ffc46b", "#f08a1c"],
  other: ["#b3aec2", "#7c7690"], none: ["#cfcbe0", "#8f8aa6"],
};
export const KIND_COLORS: Record<string, [string, string]> = {
  checking: ["#ff8cc0", "#c94b8c"], savings: ["#ffb8d6", "#e66597"], card: ["#9b8cff", "#5a45d6"],
  cash: ["#7ee8b5", "#21a874"], investment: ["#6ee7d0", "#13897e"], crypto: ["#ffc46b", "#e07a12"],
};

export const categoryColor = (c: string) => (CATEGORY_COLORS[c] ?? CATEGORY_COLORS.other)[0];

/** A category's tile: glossy squircle in its colour with the white icon. */
export function CategoryTile({ category, size = 40 }: { category: string; size?: number }) {
  const key = category && category in CATEGORY_COLORS ? category : category ? "other" : "none";
  const [a, b] = CATEGORY_COLORS[key];
  return <Tile icon={key} from={a} to={b} size={size} />;
}

export function Tile({ icon, from, to, size = 40 }: { icon: string; from: string; to: string; size?: number }) {
  return (
    <span className="tile" style={{ width: size, height: size, borderRadius: size * 0.32, background: `linear-gradient(145deg, ${from}, ${to})`, boxShadow: `0 6px 16px -6px ${to}` }}>
      <Icon name={icon} size={size * 0.56} stroke={1.9} />
    </span>
  );
}
