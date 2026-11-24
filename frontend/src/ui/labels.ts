import type { T } from "../i18n";

export const CATEGORIES = ["groceries", "restaurants", "home", "bills", "transport", "car", "health", "leisure", "travel",
  "subscriptions", "shopping", "education", "gifts", "taxes", "fees", "salary", "income", "transfers", "savings",
  "investments", "crypto", "other"];
export const KINDS = ["checking", "savings", "card", "cash", "investment", "crypto"] as const;

export const categoryLabel = (t: T, c: string) =>
  !c ? t("category.none") : t.has(`category.${c}`) ? t(`category.${c}` as never) : c.charAt(0).toUpperCase() + c.slice(1);
export const kindLabel = (t: T, k: string) => (t.has(`accounts.kind.${k}`) ? t(`accounts.kind.${k}` as never) : k);
