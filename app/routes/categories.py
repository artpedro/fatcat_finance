from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlmodel import Session, col, select

from app.category_utils import category_map_by_name, get_or_create_category_by_name, outros_id
from app.db import get_session
from app.models import Category, Expense, PixItem, Subscription
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.templates import templates

router = APIRouter(prefix="/categories", tags=["categories"])


def ordered_categories(session: Session) -> list[Category]:
    return list(session.exec(select(Category).order_by(col(Category.name))))


def build_category_field(
    session: Session,
    *,
    wrap_id: str,
    selected_id: str | None = None,
    default_name: str | None = None,
) -> dict:
    cats = ordered_categories(session)
    if not cats:
        get_or_create_category_by_name(session, "Outros")
        cats = ordered_categories(session)
    by_name = category_map_by_name(session)
    resolved = selected_id
    if not resolved or session.get(Category, resolved) is None:
        if default_name:
            rid = by_name.get(default_name)
            resolved = rid or get_or_create_category_by_name(session, default_name).id
        else:
            resolved = outros_id(session)
    return {"categories": cats, "selected_id": resolved, "wrap_id": wrap_id}


@router.get("")
def categories_page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    ctx = base_context(request, month, year, settings)
    ctx.update({"active": "categories", "categories": ordered_categories(session)})
    return templates.TemplateResponse(request, "pages/categories.html", ctx)


@router.post("/quick-create")
def quick_create(
    request: Request,
    name: str = Form(...),
    wrap_id: str = Form("category-wrap-expense"),
    session: Session = Depends(get_session),
):
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Informe o nome da categoria.")
    cat = get_or_create_category_by_name(session, cleaned)
    ctx = build_category_field(session, wrap_id=wrap_id, selected_id=cat.id)
    return templates.TemplateResponse(request, "partials/category_field.html", ctx)


@router.post("/{category_id}/rename")
def rename_category(
    category_id: str,
    request: Request,
    new_name: str = Form(...),
    session: Session = Depends(get_session),
):
    cat = session.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    cleaned = new_name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Nome inválido.")
    exists = session.exec(select(Category).where(Category.name == cleaned, Category.id != category_id)).first()
    if exists:
        raise HTTPException(status_code=400, detail="Já existe uma categoria com esse nome.")
    cat.name = cleaned
    cat.updated_at = datetime.now(UTC)
    session.add(cat)
    session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    ctx = base_context(request, month, year, settings)
    ctx.update({"active": "categories", "categories": ordered_categories(session)})
    return templates.TemplateResponse(request, "partials/categories_table.html", ctx)


@router.delete("/{category_id}")
def delete_category(category_id: str, request: Request, session: Session = Depends(get_session)):
    cat = session.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    fallback = outros_id(session)
    if category_id == fallback:
        raise HTTPException(status_code=400, detail="Não é possível excluir a categoria Outros.")
    for exp in session.exec(select(Expense).where(Expense.category_id == category_id)).all():
        exp.category_id = fallback
        session.add(exp)
    for sub in session.exec(select(Subscription).where(Subscription.category_id == category_id)).all():
        sub.category_id = fallback
        session.add(sub)
    for pix in session.exec(select(PixItem).where(PixItem.category_id == category_id)).all():
        pix.category_id = fallback
        session.add(pix)
    session.delete(cat)
    session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    ctx = base_context(request, month, year, settings)
    ctx.update({"active": "categories", "categories": ordered_categories(session)})
    return templates.TemplateResponse(request, "partials/categories_table.html", ctx)
