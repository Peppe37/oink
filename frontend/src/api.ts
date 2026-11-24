/* The API client: same origin, session cookie. Account and movement amounts are exact
   decimal strings in their own currency; reports are numbers in the user's main currency.
   Errors carry a short code (translated with useErrorText). */

export type User = { id: number; username: string; name: string; is_admin: boolean; language: string; base_currency: string; disabled: boolean; created: string };
export type Status = { configured: boolean; user: User | null; ai: boolean; ai_reads_text: boolean; banking: boolean; version: string };
export type Account = {
  id: number; name: string; kind: string; institution: string; iban: string; currency: string; source: string;
  balance: string | null; balance_at: string | null; color: string; archived: boolean; connection_id: number | null;
};
export type Tx = {
  id: number; account_id: number; day: string; amount: string; currency: string; description: string;
  counterparty: string; category: string; category_by: string; note: string; source: string;
};
export type Recurring = { name: string; monthly: number; last: number; increased: boolean; category: string; last_day: string };
export type MonthSummary = {
  month: string; income: number; spending: number; net: number; saving_rate: number | null;
  by_category: Record<string, number>; top_merchants: Record<string, number>; uncategorised: number; count: number;
};
export type Overview = {
  currency: string;
  net_worth: { currency: string; total: number; accounts: { id: number; name: string; kind: string; value: number | null }[]; missing_rates: string[] };
  month: { currency: string; current: MonthSummary; usual_spending: number | null; usual_income: number | null;
           categories: Record<string, { now: number; usual: number; delta: number }> };
  series: { month: string; income: number; spending: number }[];
  balance: { day: string; value: number }[];
  recurring: Recurring[];
  open_requests: number;
};
export type Report = { id: number; month: string; kind: string; content: string; model: string; language: string; created: string };
export type Token = { id: number; name: string; prefix: string; scopes: string[]; created: string; last_used: string | null; revoked: boolean };
export type Connection = { id: number; provider: string; label: string; status: string; valid_until: string | null; last_sync: string | null; error: string };
export type StatementRequest = { id: number; account_id: number; month: string; status: string; created: string };
export type ProviderField = { name: string; secret: boolean; placeholder: string };
export type KeyedProvider = { id: string; name: string; kind: "exchange" | "wallet" | "fintech" | "bank"; docs: string; movements: boolean; fields: ProviderField[] };
export type Providers = { banking: boolean; countries: string[]; keyed: KeyedProvider[] };
export type ImportResult = { account_id: number; added: number; duplicates: number; month: string; by_ai: boolean; estimated: boolean; balance_updated: boolean; request_closed: boolean };
export type UpdateInfo = {
  current: string; latest: { version: string; notes: string; url: string } | null; update_available: boolean; can_install: boolean;
  state: { state?: string; version?: string };
};
export type SystemInfo = { version: string; updates?: UpdateInfo; ai_model?: string; banking?: boolean; callback_url?: string; inbox?: string };

export class ApiError extends Error {
  constructor(public status: number, code: string) { super(code); }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { credentials: "same-origin", ...init, headers: { ...headers, ...(init.headers ?? {}) } });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!response.ok) {
    let detail: unknown = "generic";
    try { detail = (await response.json()).detail; } catch { /* not JSON */ }
    throw new ApiError(response.status, typeof detail === "string" ? detail.split(":")[0] : "generic");
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown) => JSON.stringify(body);
const query = (params: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)])).toString();

export const api = {
  health: () => call<{ ok: boolean; version: string }>("/health"),
  status: () => call<Status>("/auth/status"),
  setup: (body: { username: string; password: string; language: string; base_currency: string }) => call("/auth/setup", { method: "POST", body: json(body) }),
  login: (username: string, password: string) => call("/auth/login", { method: "POST", body: json({ username, password }) }),
  invite: (token: string) => call<{ username: string; name: string }>(`/auth/invite/${encodeURIComponent(token)}`),
  acceptInvite: (token: string, password: string) => call("/auth/invite", { method: "POST", body: json({ token, password }) }),
  logout: () => call("/auth/logout", { method: "POST" }),
  password: (current: string, next: string) => call("/auth/password", { method: "POST", body: json({ current, new: next }) }),
  updateMe: (body: Partial<Pick<User, "name" | "language" | "base_currency">>) => call<User>("/me", { method: "PATCH", body: json(body) }),

  overview: () => call<Overview>("/reports/overview"),
  currencies: () => call<{ fiat: string[]; crypto: string[] }>("/currencies"),
  accounts: (archived = false) => call<Account[]>(`/accounts?${query({ archived: archived ? "true" : undefined })}`),
  addAccount: (body: Partial<Omit<Account, "balance">> & { balance?: string }) => call<Account>("/accounts", { method: "POST", body: json(body) }),
  patchAccount: (id: number, body: Partial<Omit<Account, "balance">> & { balance?: string | null }) => call<Account>(`/accounts/${id}`, { method: "PATCH", body: json(body) }),
  deleteAccount: (id: number) => call(`/accounts/${id}`, { method: "DELETE" }),

  transactions: (params: Record<string, string | number | undefined>) => call<Tx[]>(`/transactions?${query(params)}`),
  addTx: (body: { account_id: number; day: string; amount: string; description: string; category?: string; note?: string }) => call<Tx>("/transactions", { method: "POST", body: json(body) }),
  patchTx: (id: number, body: { category?: string; note?: string; learn?: boolean }) => call<Tx>(`/transactions/${id}`, { method: "PATCH", body: json(body) }),
  deleteTx: (id: number) => call(`/transactions/${id}`, { method: "DELETE" }),
  categories: () => call<{ builtin: string[]; custom: string[] }>("/categories"),
  categorize: () => call<{ categorized: number }>("/transactions/categorize", { method: "POST" }),
  importFile: (file: File, accountId?: number) => {
    const form = new FormData();
    if (accountId) form.append("account_id", String(accountId));
    form.append("file", file);
    return call<ImportResult>("/imports", { method: "POST", body: form });
  },
  requests: () => call<StatementRequest[]>("/requests"),
  skipRequest: (id: number) => call(`/requests/${id}/skip`, { method: "POST" }),

  reports: () => call<Report[]>("/reports"),
  generate: (language: string) => call<Report>(`/reports/generate?${query({ language })}`, { method: "POST" }),
  ask: (question: string, language: string) => call<{ answer: string }>(`/ask?${query({ language })}`, { method: "POST", body: json({ question }) }),

  providers: () => call<Providers>("/providers"),
  connections: () => call<Connection[]>("/connections"),
  linkKey: (provider: string, credentials: Record<string, string>, label: string) => call<Connection>("/connections/key", { method: "POST", body: json({ provider, credentials, label }) }),
  banks: (country: string) => call<{ name: string; country: string; logo: string }[]>(`/connections/banks?${query({ country })}`),
  linkBank: (bank: string, country: string) => call<{ url: string }>("/connections/bank", { method: "POST", body: json({ bank, country }) }),
  sync: () => call<{ added: number }>("/connections/sync", { method: "POST" }),
  unlink: (id: number) => call(`/connections/${id}`, { method: "DELETE" }),

  scopes: () => call<string[]>("/scopes"),
  tokens: () => call<Token[]>("/tokens"),
  newToken: (name: string, scopes: string[]) => call<Token & { token: string }>("/tokens", { method: "POST", body: json({ name, scopes }) }),
  revokeToken: (id: number) => call(`/tokens/${id}`, { method: "DELETE" }),

  users: () => call<User[]>("/users"),
  addUser: (username: string, name: string) => call<{ user: User; invite_url: string }>("/users", { method: "POST", body: json({ username, name }) }),
  reinvite: (id: number) => call<{ invite_url: string }>(`/users/${id}/invite`, { method: "POST" }),
  patchUser: (id: number, body: Partial<Pick<User, "is_admin" | "disabled" | "name">>) => call<User>(`/users/${id}`, { method: "PATCH", body: json(body) }),
  deleteUser: (id: number) => call(`/users/${id}`, { method: "DELETE" }),

  system: () => call<SystemInfo>("/system"),
  installUpdate: () => call<{ ok: boolean; version: string }>("/system/update", { method: "POST" }),
  setBanking: (app_id: string, private_key: string) => call("/system/banking", { method: "PUT", body: json({ app_id, private_key }) }),
};
