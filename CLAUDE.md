# Oink — notes for coding agents

Self-hosted personal finance, multi-user. Public repo, AGPL-3.0. Code and docs in English;
the UI speaks English, Italian, German, French and Spanish (default: the device's language).

- `backend/` Python 3.12, FastAPI, SQLAlchemy 2 (SQLite), Alembic, package `oink`:
  `core/` (config from `OINK_*` env, models, money = per-currency minor units, security =
  users, sessions, invites, scoped tokens, the Vault that encrypts provider credentials,
  migrate = Alembic at startup), `providers/` (Enable Banking redirect flow; keyed providers:
  Wise, Kraken, Binance, Bitcoin/Ethereum addresses), `services/` (ledger with dedupe and
  rules, importer CSV/OFX/PDF + IBAN detection, statements + inbox, fx rates, reports =
  numbers, ai = LiteLLM, connections, updates, jobs = background loop), `api/routes.py`.
- `frontend/` React + Vite + framer-motion. `src/glass/Glass.tsx` is the glass material
  (SVG displacement lens in backdrop-filter, frosted fallback): keep the visual language.
  Phone first (tab bar, sheets); sidebar from 900 px. Icons are our own (`src/ui/icons.tsx`):
  no emoji in the UI. Every string goes through `t()` with a key in ALL five `src/i18n/*.ts`.
- Every row with money has `user_id` and every query filters on `Caller.user_id`: users never
  see each other's data (tests cover it).
- Amounts are integers in the currency's smallest unit (`core/money.py`, 8 decimals for
  crypto); the API speaks exact decimal strings. Reports convert to the user's base currency.
  The AI never computes numbers and never receives descriptions when
  `OINK_AI_SEND_DESCRIPTIONS=false`.
- Every API route declares its scope with `require("<scope>")`; things a token must never do
  use `browser_only`, instance settings `admin_only`. New scopes: `core/security.py` `SCOPES`,
  the README table and the `settings.scope.*` keys.
- A schema change ships an Alembic revision (`backend/src/oink/migrations/versions`).
- Commits: author is the GitHub noreply address, no co-author trailers.
- Every change ships with tests. Checks:
  `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
  and `cd frontend && npm run build`.
- Release: tag `vX.Y.Z` → `.github/workflows/release.yml` pushes `ghcr.io/peppe37/oink` and a
  GitHub release (running instances offer the update).
