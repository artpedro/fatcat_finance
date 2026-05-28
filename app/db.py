from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AppSettings

DB_FILE = Path(__file__).resolve().parent.parent / "fatcat.db"
DATABASE_URL = f"sqlite:///{DB_FILE}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _savingsentry_supports_yield(conn) -> bool:
    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='savingsentry'"
    ).fetchone()
    if not row or not row[0]:
        return False
    return "'yield'" in row[0] or '"yield"' in row[0]


def _migrate_savingsentry_direction_constraint(conn) -> None:
    if _savingsentry_supports_yield(conn):
        return
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.exec_driver_sql("DROP TABLE IF EXISTS savingsentry_new")
    conn.exec_driver_sql(
        """
        CREATE TABLE savingsentry_new (
            id TEXT NOT NULL PRIMARY KEY,
            group_id TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            amount FLOAT NOT NULL,
            direction VARCHAR NOT NULL,
            source_type VARCHAR NOT NULL DEFAULT '',
            source_ref_id VARCHAR NOT NULL DEFAULT '',
            notes VARCHAR NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            CONSTRAINT ck_save_direction CHECK (direction IN ('deposit', 'withdrawal', 'yield')),
            FOREIGN KEY(group_id) REFERENCES savingsgroup (id)
        )
        """
    )
    conn.exec_driver_sql(
        """
        INSERT INTO savingsentry_new
        (id, group_id, entry_date, amount, direction, source_type, source_ref_id, notes, created_at, updated_at)
        SELECT id, group_id, entry_date, amount, direction, source_type, source_ref_id, notes, created_at, updated_at
        FROM savingsentry
        """
    )
    conn.exec_driver_sql("DROP TABLE savingsentry")
    conn.exec_driver_sql("ALTER TABLE savingsentry_new RENAME TO savingsentry")
    conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def _run_migrations() -> None:
    with engine.begin() as conn:
        if _column_exists(conn, "savingsgroup", "cdi_pct") is False:
            conn.exec_driver_sql("ALTER TABLE savingsgroup ADD COLUMN cdi_pct FLOAT NOT NULL DEFAULT 100.0")
        _migrate_savingsentry_direction_constraint(conn)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _run_migrations()
    with Session(engine) as session:
        settings = session.exec(select(AppSettings)).first()
        if settings is None:
            session.add(AppSettings())
            session.commit()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
