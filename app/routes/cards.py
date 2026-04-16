from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Card, Expense, Subscription
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.services.finance import card_total, expenses_for_month, outstanding_for_card, subscription_costs_by_method
from app.templates import brl, templates

router = APIRouter(prefix="/cards", tags=["cards"])


def _rows(session: Session, month: int, year: int) -> list[dict]:
    cards = session.exec(select(Card)).all()
    expenses = session.exec(select(Expense)).all()
    subscriptions = session.exec(select(Subscription)).all()
    card_subs, _ = subscription_costs_by_method(subscriptions, month, year)
    used_by_sub = {sub.card_id for sub in subscriptions if sub.payment_method == "card" and sub.card_id}
    cards_by_id = {c.id: c for c in cards}
    month_exp = expenses_for_month(expenses, cards_by_id, month, year)
    rows: list[dict] = []
    for card in cards:
        card.is_used_by_subscriptions = card.id in used_by_sub
        session.add(card)
        total = card_total(card, month_exp, card_subs)
        outstanding = outstanding_for_card(card, expenses, month, year)
        rows.append(
            {
                "card": card,
                "total_fmt": brl(total),
                "outstanding_fmt": brl(outstanding),
                "limit_fmt": brl(card.limit_amount),
            }
        )
    session.commit()
    return rows


@router.get("")
def cards_page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"active": "cards", "cards_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "pages/cards.html", context)


@router.get("/form")
def card_form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"card": None})
    return templates.TemplateResponse(request, "partials/card_form.html", context)


@router.get("/form/{card_id}")
def card_form_edit(card_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    card = session.get(Card, card_id)
    context = base_context(request, month, year, settings)
    context.update({"card": card})
    return templates.TemplateResponse(request, "partials/card_form.html", context)


@router.get("/form-clear", response_class=HTMLResponse)
def clear_form() -> str:
    return ""


@router.post("/save")
def save_card(
    request: Request,
    card_id: str = Form(""),
    name: str = Form(...),
    closing_day: int = Form(0),
    due_day: int = Form(5),
    color: str = Form("#DB8A74"),
    limit_amount: float = Form(0),
    maintenance_type: str = Form("none"),
    maintenance_amount: float = Form(0),
    session: Session = Depends(get_session),
):
    card = session.get(Card, card_id) if card_id else None
    if card is None:
        card = Card(name=name)
    card.name = name.strip()
    card.closing_day = int(closing_day or 0)
    card.due_day = int(due_day or 5)
    card.color = color
    card.limit_amount = float(limit_amount or 0)
    card.maintenance_type = maintenance_type
    card.maintenance_amount = float(maintenance_amount or 0)
    card.updated_at = datetime.utcnow()
    session.add(card)
    session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)


@router.delete("/{card_id}")
def delete_card(card_id: str, request: Request, session: Session = Depends(get_session)):
    card = session.get(Card, card_id)
    if card:
        expenses = session.exec(select(Expense).where(Expense.card_id == card_id)).all()
        for expense in expenses:
            session.delete(expense)
        subscriptions = session.exec(select(Subscription).where(Subscription.card_id == card_id)).all()
        for sub in subscriptions:
            if sub.payment_method == "card":
                session.delete(sub)
        session.delete(card)
        session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)

