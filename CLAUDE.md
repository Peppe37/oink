# Oink — notes for coding agents

Self-hosted personal finance. Public repo, AGPL-3.0. UI text in Italian, code and docs in English.

- `backend/` Python 3.12, FastAPI, SQLAlchemy 2 (SQLite), package `oink`:
  `core/` (config from `OINK_*` env, models, security: session cookie + scoped tokens),
  `services/` (ledger with dedupe and rules, importer CSV/PDF, reports = numbers, ai = LiteLLM,
  jobs = background loop), `providers/enablebanking.py`, `api/routes.py`.
- `frontend/` React + Vite + framer-motion. `src/glass/Glass.tsx` is the glass material
  (SVG displacement lens in backdrop-filter, frosted fallback). Keep the visual language.
- Money is integer cents everywhere. Numbers are computed in `services/reports.py`; the AI only
  writes words about them and must never receive descriptions when `OINK_AI_SEND_DESCRIPTIONS=false`.
- Every API route declares its scope with `require("<scope>")`; things a token must never do use
  `owner_only`. New scopes go in `core/security.py` `SCOPES` and the README table.
- Every change ships with tests. Checks:
  `cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && uv run pytest -q`
  and `cd frontend && npm run build`.
