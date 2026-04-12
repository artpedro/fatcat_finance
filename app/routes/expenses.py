from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Card, Expense
from app.routes.common import base_context, current_period, get_settings
from app.services.finance import billing_start, expenses_for_month, mkey
from app.templates import brl, templates

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _expense_rows(session: Session, month: int, year: int) -> tuple[list[dict], dict[str, str], list[Card]]:
    cards = session.exec(select(Card)).all()
    cards_by_id = {card.id: card for card in cards}
    expenses = session.exec(select(Expense)).all()
    month_rows = expenses_for_month(expenses, cards_by_id, month, year)
    month_by_id = {row["expense"].id: row for row in month_rows}
    rows: list[dict] = []
    selected_key = mkey(month, year)
    for expense in sorted(expenses, key=lambda e: (e.purchase_year, e.purchase_month, e.purchase_day), reverse=True):
        card = cards_by_id.get(expense.card_id)
        bm, by = billing_start(expense, card)
        row = month_by_id.get(expense.id)
        if expense.type == "debit":
            active = expense.purchase_month == month and expense.purchase_year == year
            status = "No mês" if active else "Fora do mês"
            month_amount = expense.amount_total if active else 0
        else:
            start = mkey(bm, by)
            end = start + expense.installments - 1
            if selected_key < start:
                status = "Aguardando"
                month_amount = 0
            elif selected_key > end:
                status = "Concluído"
                month_amount = 0
            else:
                current_inst = selected_key - start + 1
                status = f"{current_inst}/{expense.installments}"
                month_amount = row["month_amount"] if row else 0
        rows.append(
            {
                "expense": expense,
                "month_fmt": brl(month_amount),
                "total_fmt": brl(expense.amount_total),
                "status": status,
            }
        )
    return rows, {card.id: card.name for card in cards}, cards


@router.get("")
def expenses_page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, cards_map, cards = _expense_rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"active": "expenses", "expense_rows": rows, "cards_map": cards_map, "cards": cards})
    return templates.TemplateResponse(request, "pages/expenses.html", context)


@router.get("/form")
def expense_form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    cards = session.exec(select(Card)).all()
    today = datetime.utcnow().date().isoformat()
    context = base_context(request, month, year, settings)
    context.update({"expense": None, "cards": cards, "purchase_date": today})
    return templates.TemplateResponse(request, "partials/expense_form.html", context)


@router.get("/form/{expense_id}")
def expense_form_edit(expense_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    cards = session.exec(select(Card)).all()
    expense = session.get(Expense, expense_id)
    purchase_date = ""
    if expense:
        purchase_date = datetime(expense.purchase_year, expense.purchase_month + 1, expense.purchase_day).date().isoformat()
    context = base_context(request, month, year, settings)
    context.update({"expense": expense, "cards": cards, "purchase_date": purchase_date})
    return templates.TemplateResponse(request, "partials/expense_form.html", context)


@router.get("/form-clear", response_class=HTMLResponse)
def clear_expense_form() -> str:
    return ""


@router.post("/save")
def save_expense(
    request: Request,
    expense_id: str = Form(""),
    description: str = Form(...),
    exp_type: str = Form("credit"),
    card_id: str = Form(...),
    amount_total: float = Form(...),
    installments: int = Form(1),
    purchase_date: str = Form(...),
    category: str = Form("Outros"),
    session: Session = Depends(get_session),
):
    expense = session.get(Expense, expense_id) if expense_id else None
    if expense is None:
        expense = Expense(type=exp_type, card_id=card_id, description=description, amount_total=amount_total, purchase_day=1, purchase_month=0, purchase_year=2024)
    date_obj = datetime.strptime(purchase_date, "%Y-%m-%d")
    expense.description = description.strip()
    expense.type = exp_type
    expense.card_id = card_id
    expense.amount_total = float(amount_total)
    expense.installments = 1 if exp_type == "debit" else max(1, int(installments))
    expense.purchase_day = date_obj.day
    expense.purchase_month = date_obj.month - 1
    expense.purchase_year = date_obj.year
    expense.category = category
    expense.updated_at = datetime.utcnow()
    session.add(expense)
    session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, cards_map, _cards = _expense_rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"expense_rows": rows, "cards_map": cards_map})
    return templates.TemplateResponse(request, "partials/expenses_table.html", context)


@router.delete("/{expense_id}")
def delete_expense(expense_id: str, request: Request, session: Session = Depends(get_session)):
    expense = session.get(Expense, expense_id)
    if expense:
        session.delete(expense)
        session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, cards_map, _cards = _expense_rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"expense_rows": rows, "cards_map": cards_map})
    return templates.TemplateResponse(request, "partials/expenses_table.html", context)

