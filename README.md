# FatCat

Local personal finance app using FastAPI, HTMX, Tailwind CSS, and SQLite.

## Run with uv

```bash
uv sync
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
