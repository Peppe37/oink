/* The API client: same origin, session cookie. Money is integer cents everywhere. */

export type Account = {
  id: number; name: string; kind: string; institution: string; currency: string; source: string;
  balance_cents: number | null; balance_at: string | null; color: string; archived: boolean; connection_id: number | null;
};
export type Tx = {
  id: number; account_id: number; day: string; amount_cents: number; currency: string; description: string;
  counterparty: string; category: string; category_by: string; note: string; source: string;
};
export type Recurring = { name: string; monthly_cents: number; last_cents: number; increased: boolean; category: string; last_day: string };
export type MonthSummary = {
  month: string; income_cents: number; spending_cents: number; net_cents: number; saving_rate: number | null;
  by_category: Record<string, number>; top_merchants: Record<string, number>; uncategorised: number; count: number;
};
export type Overview = {
  net_worth: { total_cents: number; accounts: { id: number; name: string; kind: string; balance_cents: number | null }[] };
  month: { current: MonthSummary; usual_spending_cents: number | null; usual_income_cents: number | null;
           categories: Record<string, { now_cents: number; usual_cents: number; delta_cents: number }> };
  series: { month: string; income_cents: number; spending_cents: number }[];
  balance: { day: string; cents: number }[];
  recurring: Recurring[];
  open_requests: number;
};
export type Report = { id: number; month: string; kind: string; content: string; model: string; created: string };
export type Token = { id: number; name: string; prefix: string; scopes: string[]; created: string; last_used: string | null; revoked: boolean };
export type Connection = { id: number; provider: string; institution: string; status: string; valid_until: string | null; last_sync: string | null; error: string };
export type StatementRequest = { id: number; account_id: number; month: string; status: string; created: string };
export type Status = { configured: boolean; logged_in: boolean; ai: boolean; banking: boolean; currency: string };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(`/api${path}`, { credentials: "same-origin", ...init, headers: { ...headers, ...(init.headers ?? {}) } });
  if (!response.ok) {
    let detail = response.statusText;
    try { detail = (await response.json()).detail ?? detail; } catch { /* not JSON */ }
    if (typeof detail !== "string") detail = "Dati non validi";
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);

export const api = {
  status: () => call<Status>("/auth/status"),
  setup: (password: string) => call("/auth/setup", { method: "POST", body: json({ password }) }),
  login: (password: string) => call("/auth/login", { method: "POST", body: json({ password }) }),
  logout: () => call("/auth/logout", { method: "POST" }),
  password: (current: string, next: string) => call("/auth/password", { method: "POST", body: json({ current, new: next }) }),
  overview: () => call<Overview>("/reports/overview"),
  accounts: () => call<Account[]>("/accounts"),
  addAccount: (body: Partial<Account>) => call<Account>("/accounts", { method: "POST", body: json(body) }),
  patchAccount: (id: number, body: Partial<Account>) => call<Account>(`/accounts/${id}`, { method: "PATCH", body: json(body) }),
  transactions: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
    return call<Tx[]>(`/transactions?${q}`);
  },
  patchTx: (id: number, body: { category?: string; note?: string }) => call<Tx>(`/transactions/${id}`, { method: "PATCH", body: json(body) }),
  addTx: (body: Partial<Tx>) => call<Tx>("/transactions", { method: "POST", body: json(body) }),
  categories: () => call<string[]>("/categories"),
  categorize: () => call<{ categorized: number }>("/transactions/categorize", { method: "POST" }),
  importFile: (accountId: number, file: File) => {
    const form = new FormData();
    form.append("account_id", String(accountId));
    form.append("file", file);
    return call<{ added: number; duplicates: number; month: string; by_ai: boolean; request_closed: boolean }>("/imports", { method: "POST", body: form });
  },
  requests: () => call<StatementRequest[]>("/requests"),
  skipRequest: (id: number) => call(`/requests/${id}/skip`, { method: "POST" }),
  reports: () => call<Report[]>("/reports"),
  generate: (month?: string) => call<Report>(`/reports/generate${month ? `?month=${month}` : ""}`, { method: "POST" }),
  ask: (question: string) => call<{ answer: string }>("/ask", { method: "POST", body: json({ question }) }),
  scopes: () => call<Record<string, string>>("/scopes"),
  tokens: () => call<Token[]>("/tokens"),
  newToken: (name: string, scopes: string[]) => call<Token & { token: string }>("/tokens", { method: "POST", body: json({ name, scopes }) }),
  revokeToken: (id: number) => call(`/tokens/${id}`, { method: "DELETE" }),
  banks: () => call<{ name: string; country: string; logo: string }[]>("/banks"),
  connections: () => call<Connection[]>("/banks/connections"),
  connect: (bank: string) => { const f = new FormData(); f.append("bank", bank); return call<{ url: string }>("/banks/connect", { method: "POST", body: f }); },
  sync: () => call<{ added: number }>("/banks/sync", { method: "POST" }),
};

export const eur = (cents: number | null | undefined, opts: { sign?: boolean; compact?: boolean } = {}) => {
  if (cents === null || cents === undefined) return "—";
  const value = cents / 100;
  const text = new Intl.NumberFormat("it-IT", {
    style: "currency", currency: "EUR", maximumFractionDigits: opts.compact ? 0 : 2,
    signDisplay: opts.sign ? "exceptZero" : "auto",
    // Italian skips the separator for 4 digits («1411»): always group, so columns line up.
    useGrouping: "always" as unknown as boolean,
  }).format(value);
  return text;
};

export const monthName = (month: string) => {
  const [y, m] = month.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("it-IT", { month: "long", year: "numeric" });
};

export const CATEGORY_STYLE: Record<string, { emoji: string; color: string }> = {
  spesa: { emoji: "🛒", color: "#5ee6c4" }, ristoranti: { emoji: "🍝", color: "#ffb38a" }, casa: { emoji: "🏠", color: "#8b7bff" },
  bollette: { emoji: "💡", color: "#ffd166" }, trasporti: { emoji: "🚆", color: "#6ec8ff" }, auto: { emoji: "🚗", color: "#7aa2ff" },
  salute: { emoji: "🩺", color: "#ff7a90" }, svago: { emoji: "🎟️", color: "#ff6fb1" }, viaggi: { emoji: "✈️", color: "#4fd1ff" },
  abbonamenti: { emoji: "🔁", color: "#c58bff" }, shopping: { emoji: "🛍️", color: "#ff9ecb" }, istruzione: { emoji: "📚", color: "#9be15d" },
  regali: { emoji: "🎁", color: "#ff8fa3" }, tasse: { emoji: "🏛️", color: "#b8b8c8" }, commissioni: { emoji: "🧾", color: "#a0a0b0" },
  stipendio: { emoji: "💼", color: "#46e3a0" }, entrate: { emoji: "💶", color: "#46e3a0" }, trasferimenti: { emoji: "↔️", color: "#9aa0b4" },
  risparmio: { emoji: "🐷", color: "#ffb3d1" }, investimenti: { emoji: "📈", color: "#5ee6c4" }, altro: { emoji: "•", color: "#9a95a8" },
};
export const catStyle = (c: string) => CATEGORY_STYLE[c] ?? { emoji: "❓", color: "#9a95a8" };
