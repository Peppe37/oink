# Contributing

Thanks for helping. Oink is meant for everybody who self-hosts, not for one household: keep
changes general (no bank, country or person hard-coded where a setting or a generic reader will
do), local first and honest about what it does with data.

- Small fixes: open a pull request. Bigger ideas: open an issue first and say what problem it solves.
- Backend checks (from `backend/`): `uv run ruff check src tests && uv run ruff format src tests && uv run mypy src && uv run pytest -q`.
- Frontend: `npm run build` in `frontend/`. User-facing text goes through `frontend/src/i18n/` in all five languages.
- A bug fix comes with a test that fails without it. A schema change comes with an Alembic revision
  that only adds columns (`op.add_column`); never rebuild a parent table, SQLite would cascade-delete the
  rows of its children. Rehearse it on a copy of a real database.
- Statement readers: add the layout to `backend/src/oink/services/layouts.py` with a test on a synthetic
  sample; never commit real statements or personal data.
- Commits: short imperative message, no secrets, and use an identity you're happy to make public.

By contributing you agree your work is released under the AGPL-3.0 like the rest of the project.
