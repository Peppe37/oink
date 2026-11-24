/* Languages: English by default, the device's language when Oink speaks it, or the one the
   user picked in Settings. Also every number, date and amount is formatted for that locale,
   in any currency, crypto included. */

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { de } from "./de";
import { en, type Dict, type Key } from "./en";
import { es } from "./es";
import { fr } from "./fr";
import { it } from "./it";

export const LANGUAGES = { en: "English", it: "Italiano", de: "Deutsch", fr: "Français", es: "Español" } as const;
export type Lang = keyof typeof LANGUAGES;
const DICTS: Record<Lang, Dict> = { en, it, de, fr, es };
const CRYPTO_SIGNS: Record<string, string> = { BTC: "₿", ETH: "Ξ" };

export function deviceLanguage(): Lang {
  for (const tag of navigator.languages ?? [navigator.language]) {
    const code = tag.slice(0, 2).toLowerCase();
    if (code in DICTS) return code as Lang;
  }
  return "en";
}

export function resolveLanguage(chosen: string | undefined | null): Lang {
  return chosen && chosen in DICTS ? (chosen as Lang) : deviceLanguage();
}

type Vars = Record<string, string | number>;
export type T = ((key: Key, vars?: Vars) => string) & { has: (key: string) => boolean };

function fill(text: string, vars?: Vars): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

function makeT(lang: Lang): T {
  const dict = DICTS[lang];
  const plural = new Intl.PluralRules(lang);
  const fn = ((key: Key, vars?: Vars) => {
    let k = key as string;
    if (vars && typeof vars.count === "number") {
      const form = plural.select(vars.count) === "one" ? "_one" : "_other";
      if (`${k}${form}` in dict) k = `${k}${form}`;
    }
    return fill((dict as Record<string, string>)[k] ?? (en as Record<string, string>)[k] ?? k, vars);
  }) as T;
  fn.has = (key: string) => key in dict;
  return fn;
}

export type Fmt = ReturnType<typeof makeFmt>;

function isFiat(code: string) {
  try {
    new Intl.NumberFormat("en", { style: "currency", currency: code });
    return code.length === 3 && !(code in CRYPTO_SIGNS);
  } catch {
    return false;
  }
}

function makeFmt(lang: Lang) {
  const locale = lang === "en" ? (navigator.language.startsWith("en") ? navigator.language : "en-GB") : lang;
  const cache = new Map<string, Intl.NumberFormat>();
  const nf = (key: string, opts: Intl.NumberFormatOptions) => {
    const id = key + JSON.stringify(opts);
    if (!cache.has(id)) cache.set(id, new Intl.NumberFormat(locale, { useGrouping: "always" as unknown as boolean, ...opts }));
    return cache.get(id)!;
  };
  return {
    locale,
    /** An amount (decimal string or number) in `currency`; crypto shows up to 8 decimals. */
    money(value: string | number | null | undefined, currency: string, o: { sign?: boolean; compact?: boolean } = {}) {
      if (value === null || value === undefined || value === "") return "—";
      const n = typeof value === "number" ? value : Number(value);
      const signDisplay = o.sign ? "exceptZero" : "auto";
      if (isFiat(currency)) {
        const digits = o.compact ? { maximumFractionDigits: 0 } : {};
        return nf(currency, { style: "currency", currency, signDisplay, ...digits }).format(n);
      }
      const abs = Math.abs(n);
      const max = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
      const text = nf("crypto", { minimumFractionDigits: 0, maximumFractionDigits: max, signDisplay }).format(n);
      return `${text} ${CRYPTO_SIGNS[currency] ?? currency}`;
    },
    number(n: number, digits = 0) {
      return nf("n", { maximumFractionDigits: digits }).format(n);
    },
    percent(n: number) {
      return nf("p", { style: "percent", maximumFractionDigits: 0 }).format(n);
    },
    date(iso: string, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) {
      return new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(locale, opts);
    },
    month(month: string, style: "long" | "short" | "narrow" = "long") {
      const [y, m] = month.split("-").map(Number);
      return new Date(y, m - 1, 1).toLocaleDateString(locale, style === "long" ? { month: "long", year: "numeric" } : { month: style });
    },
    relative(iso: string | null) {
      if (!iso) return "—";
      const seconds = (new Date(iso).getTime() - Date.now()) / 1000;
      const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
      const steps: [Intl.RelativeTimeFormatUnit, number][] = [["minute", 60], ["hour", 3600], ["day", 86400], ["month", 2592000]];
      let unit: Intl.RelativeTimeFormatUnit = "second";
      let value = seconds;
      for (const [u, size] of steps) if (Math.abs(seconds) >= size) { unit = u; value = seconds / size; }
      return rtf.format(Math.round(value), unit);
    },
  };
}

type Ctx = { lang: Lang; t: T; fmt: Fmt };
const I18n = createContext<Ctx | null>(null);

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo(() => ({ lang, t: makeT(lang), fmt: makeFmt(lang) }), [lang]);
  document.documentElement.lang = lang;
  return <I18n.Provider value={value}>{children}</I18n.Provider>;
}

export function useI18n(): Ctx {
  const ctx = useContext(I18n);
  if (!ctx) throw new Error("useI18n outside I18nProvider");
  return ctx;
}

/** A translated error message from an API error code. */
export function useErrorText() {
  const { t } = useI18n();
  return useCallback((e: unknown) => {
    const code = e instanceof Error ? e.message : String(e);
    const key = `error.${code}`;
    return t.has(key) ? t(key as Key) : t("error.generic");
  }, [t]);
}

export type { Key };
