# FatCat

Local personal finance app using FastAPI, HTMX, Tailwind CSS, and SQLite.
Credit-card purchases, debits and subscriptions are grouped by **billing
cycle** (fatura) instead of calendar month; paid bills are frozen into the
`BillCycle` / `BillCycleLine` tables so historical totals stay stable.

## Run with uv

```bash
uv sync
uv run uvicorn app.main:app --reload
```

## Fresh start

This codebase migrated to cycle-based billing. There is no automatic
backfill of old data: delete `fatcat.db` (if present) before the first run
so `init_db()` creates the new schema cleanly.

```bash
rm -f fatcat.db
uv run uvicorn app.main:app --reload
```

## Tests

```bash
uv run pytest -q
```

## Seed test data

```bash
PYTHONPATH=. uv run python -m app.seed --reset
```
