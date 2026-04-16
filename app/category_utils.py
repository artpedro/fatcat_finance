from __future__ import annotations

from sqlmodel import Session, select

from app.models import Category

DEFAULT_CATEGORY_NAMES = [
    "Alimentação",
    "Transporte",
    "Saúde",
    "Lazer",
    "Vestuário",
    "Educação",
    "Casa",
    "Assinatura",
    "Mercado",
    "Outros",
    "Compra",
    "Serviço",
]


def seed_default_categories(session: Session) -> None:
    existing = session.exec(select(Category)).first()
    if existing is not None:
        return
    for name in DEFAULT_CATEGORY_NAMES:
        session.add(Category(name=name))
    session.commit()


def category_map_by_name(session: Session) -> dict[str, str]:
    return {c.name: c.id for c in session.exec(select(Category)).all()}


def category_map_by_id(session: Session) -> dict[str, str]:
    return {c.id: c.name for c in session.exec(select(Category)).all()}


def get_or_create_category_by_name(session: Session, name: str) -> Category:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Nome de categoria vazio")
    found = session.exec(select(Category).where(Category.name == cleaned)).first()
    if found:
        return found
    cat = Category(name=cleaned)
    session.add(cat)
    session.commit()
    session.refresh(cat)
    return cat


def outros_id(session: Session) -> str:
    cat = session.exec(select(Category).where(Category.name == "Outros")).first()
    if cat:
        return cat.id
    cat = get_or_create_category_by_name(session, "Outros")
    return cat.id


def parse_category_id(session: Session, raw: str | None) -> str:
    """Validate category id from forms; rejects placeholder used for inline create."""
    cid = (raw or "").strip()
    if not cid or cid == "__new__":
        raise ValueError("Selecione uma categoria ou crie uma nova antes de salvar.")
    if session.get(Category, cid) is None:
        raise ValueError("Categoria inválida.")
    return cid
