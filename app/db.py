from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import AppSettings

DB_FILE = Path(__file__).resolve().parent.parent / "fatcat.db"
DATABASE_URL = f"sqlite:///{DB_FILE}"

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        settings = session.exec(select(AppSettings)).first()
        if settings is None:
            session.add(AppSettings())
            session.commit()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

