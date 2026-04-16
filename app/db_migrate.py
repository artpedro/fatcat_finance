"""SQLite column backfill for category_id (legacy string columns)."""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.category_utils import get_or_create_category_by_name, seed_default_categories
from app.models import Category, Expense, PixItem, Subscription


def _drop_sqlite_column(engine: Engine, table: str, column: str) -> None:
    """Remove legacy columns so new rows only need category_id (ORM no longer maps old text fields)."""
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column not in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))


def migrate_category_columns(engine: Engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    with Session(engine) as session:
        seed_default_categories(session)
        session.commit()

    def column_names(table: str) -> set[str]:
        if table not in tables:
            return set()
        return {c["name"] for c in insp.get_columns(table)}

    def add_column(table: str, col: str) -> None:
        nonlocal insp, tables
        cols = column_names(table)
        if col in cols:
            return
        with engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col}" VARCHAR'))
        insp = inspect(engine)
        tables = set(insp.get_table_names())

    with Session(engine) as session:
        name_to_id = {c.name: c.id for c in session.exec(select(Category)).all()}

    def resolve_id(legacy: str | None) -> str:
        if not legacy or not str(legacy).strip():
            key = "Outros"
        else:
            key = str(legacy).strip()
        cid = name_to_id.get(key)
        if cid:
            return cid
        with Session(engine) as session:
            cat = get_or_create_category_by_name(session, key)
            name_to_id[key] = cat.id
            return cat.id

    # expense: legacy "category" text -> category_id
    exp_cols = column_names(Expense.__tablename__)
    if Expense.__tablename__ in tables:
        if "category_id" not in exp_cols:
            add_column(Expense.__tablename__, "category_id")
            exp_cols = column_names(Expense.__tablename__)
        if "category" in exp_cols:
            with engine.connect() as conn:
                rows = conn.execute(text(f'SELECT id, category FROM "{Expense.__tablename__}"')).fetchall()
            with engine.begin() as conn:
                for row in rows:
                    eid, legacy = row[0], row[1]
                    cid = resolve_id(legacy)
                    conn.execute(
                        text(f'UPDATE "{Expense.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": cid, "id": eid},
                    )
        elif "category_id" in exp_cols:
            # Fresh table might have NULL category_id
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f'SELECT id FROM "{Expense.__tablename__}" WHERE category_id IS NULL OR category_id = ""'
                    )
                ).fetchall()
            outros = resolve_id("Outros")
            with engine.begin() as conn:
                for (eid,) in rows:
                    conn.execute(
                        text(f'UPDATE "{Expense.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": outros, "id": eid},
                    )

    # subscription: legacy pix_category
    sub_cols = column_names(Subscription.__tablename__)
    if Subscription.__tablename__ in tables:
        if "category_id" not in sub_cols:
            add_column(Subscription.__tablename__, "category_id")
            sub_cols = column_names(Subscription.__tablename__)
        if "pix_category" in sub_cols:
            with engine.connect() as conn:
                rows = conn.execute(text(f'SELECT id, pix_category FROM "{Subscription.__tablename__}"')).fetchall()
            with engine.begin() as conn:
                for row in rows:
                    sid, legacy = row[0], row[1]
                    cid = resolve_id(legacy)
                    conn.execute(
                        text(f'UPDATE "{Subscription.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": cid, "id": sid},
                    )
        elif "category_id" in sub_cols:
            outros = resolve_id("Outros")
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f'SELECT id FROM "{Subscription.__tablename__}" WHERE category_id IS NULL OR category_id = ""'
                    )
                ).fetchall()
            with engine.begin() as conn:
                for (sid,) in rows:
                    conn.execute(
                        text(f'UPDATE "{Subscription.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": outros, "id": sid},
                    )

    # pixitem: legacy category
    pix_cols = column_names(PixItem.__tablename__)
    if PixItem.__tablename__ in tables:
        if "category_id" not in pix_cols:
            add_column(PixItem.__tablename__, "category_id")
            pix_cols = column_names(PixItem.__tablename__)
        if "category" in pix_cols:
            with engine.connect() as conn:
                rows = conn.execute(text(f'SELECT id, category FROM "{PixItem.__tablename__}"')).fetchall()
            with engine.begin() as conn:
                for row in rows:
                    pid, legacy = row[0], row[1]
                    cid = resolve_id(legacy)
                    conn.execute(
                        text(f'UPDATE "{PixItem.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": cid, "id": pid},
                    )
        elif "category_id" in pix_cols:
            outros = resolve_id("Outros")
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f'SELECT id FROM "{PixItem.__tablename__}" WHERE category_id IS NULL OR category_id = ""'
                    )
                ).fetchall()
            with engine.begin() as conn:
                for (pid,) in rows:
                    conn.execute(
                        text(f'UPDATE "{PixItem.__tablename__}" SET category_id = :cid WHERE id = :id'),
                        {"cid": outros, "id": pid},
                    )

    # Legacy NOT NULL text columns are no longer on SQLModel; drop them so INSERTs only set category_id.
    _drop_sqlite_column(engine, Expense.__tablename__, "category")
    _drop_sqlite_column(engine, Subscription.__tablename__, "pix_category")
    _drop_sqlite_column(engine, PixItem.__tablename__, "category")


def normalize_pixitem_recurring(engine: Engine) -> None:
    """NULL is_recurring would be falsy in Python but ambiguous in reports; default to one-off (0)."""
    insp = inspect(engine)
    if PixItem.__tablename__ not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns(PixItem.__tablename__)}
    if "is_recurring" not in cols:
        return
    with engine.begin() as conn:
        conn.execute(text(f'UPDATE "{PixItem.__tablename__}" SET is_recurring = 0 WHERE is_recurring IS NULL'))
