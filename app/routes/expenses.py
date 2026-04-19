from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.category_utils import category_map_by_id, parse_category_id
from app.db import get_session
from app.models import Card, Expense, PixItem, Subscription
from app.routes.categories import build_category_field
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.services.finance import (
    billing_start,
    expenses_for_month,
    fmt_month,
    is_subscription_active,
    mkey,
    _truthy,
)
from app.templates import brl, templates

router = APIRouter(prefix="/expenses", tags=["expenses"])

# f_pay: o que exibir (PIX/Cartão × compras/assinaturas). Legado: pix → pix_sub, card → card_all.
LANCAMENTOS_F_PAY = frozenset(
    {"pix_sub", "pix_buy", "pix_all", "card_sub", "card_buy", "card_all"}
)


def normalize_lancamentos_f_pay(raw: str) -> str:
    if raw == "pix":
        return "pix_sub"
    if raw == "card":
        return "card_all"
    if raw in LANCAMENTOS_F_PAY:
        return raw
    return ""


def expenses_list_query(request: Request, month: int, year: int) -> str:
    """Query string for month, year and lanzamentos filters (HTMX links, saves)."""
    parts: dict[str, str] = {"month": str(month), "year": str(year)}
    f_card = request.query_params.get("f_card", "")
    if f_card:
        parts["f_card"] = f_card
    if request.query_params.get("period", "month") == "all":
        parts["period"] = "all"
    f_pay = normalize_lancamentos_f_pay(request.query_params.get("f_pay", ""))
    if f_pay:
        parts["f_pay"] = f_pay
    return urlencode(parts)


def _lancamentos_filter_parts(
    f_pay: str,
) -> tuple[bool, bool, frozenset[str] | None]:
    """(include_expenses, include_pix_items, subscription_methods). None = todas assinaturas."""
    if f_pay == "":
        return True, True, None
    if f_pay == "pix_sub":
        return False, False, frozenset({"pix"})
    if f_pay == "pix_buy":
        return False, True, frozenset()
    if f_pay == "pix_all":
        return False, True, frozenset({"pix"})
    if f_pay == "card_sub":
        return False, False, frozenset({"card"})
    if f_pay == "card_buy":
        return True, False, frozenset()
    if f_pay == "card_all":
        return True, False, frozenset({"card"})
    return True, True, None


def _expense_kind_rows(
    session: Session,
    month: int,
    year: int,
    card_filter: str,
    period_filter: str,
    include: bool,
) -> list[dict]:
    if not include:
        return []
    cat_names = category_map_by_id(session)
    cards = session.exec(select(Card)).all()
    cards_by_id = {card.id: card for card in cards}
    expenses = session.exec(select(Expense)).all()
    if card_filter:
        expenses = [expense for expense in expenses if expense.card_id == card_filter]
    month_rows = expenses_for_month(expenses, cards_by_id, month, year)
    month_by_id = {row["expense"].id: row for row in month_rows}
    rows: list[dict] = []
    selected_key = mkey(month, year)
    for expense in sorted(expenses, key=lambda e: (e.purchase_year, e.purchase_month, e.purchase_day), reverse=True):
        active = False
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
                active = True
        if period_filter == "month" and not active:
            continue
        pay = f"Cartão: {card.name}" if card else "—"
        rows.append(
            {
                "kind": "expense",
                "sort_key": (expense.purchase_year, expense.purchase_month, expense.purchase_day, 0),
                "day_month": f"{expense.purchase_day:02d}/{expense.purchase_month + 1:02d}",
                "description": expense.description,
                "purchase_type": expense.type,
                "payment_label": pay,
                "card_short": card.name if card else "—",
                "month_fmt": brl(month_amount),
                "total_fmt": brl(expense.amount_total),
                "status": status,
                "category": cat_names.get(expense.category_id, "—"),
                "expense": expense,
                "subscription": None,
            }
        )
    return rows


def _subscription_kind_rows(
    session: Session,
    month: int,
    year: int,
    card_filter: str,
    period_filter: str,
    methods: frozenset[str] | None,
) -> list[dict]:
    if methods is not None and len(methods) == 0:
        return []
    cat_names = category_map_by_id(session)
    cards_by_id = {c.id: c for c in session.exec(select(Card)).all()}
    subscriptions = session.exec(select(Subscription)).all()
    if methods is not None:
        subscriptions = [s for s in subscriptions if s.payment_method in methods]
    if card_filter:
        subscriptions = [
            s
            for s in subscriptions
            if s.payment_method == "card" and s.card_id == card_filter
        ]
    rows: list[dict] = []
    for sub in sorted(subscriptions, key=lambda s: (s.start_year, s.start_month, s.billing_day), reverse=True):
        active = is_subscription_active(sub, month, year)
        if period_filter == "month" and not active:
            continue
        month_amount = sub.amount_monthly if active else 0.0
        if sub.is_indefinite:
            status = "Assinatura ativa" if active else "Fora do mês"
        else:
            status = "Ativa neste mês" if active else "Fora do período"
        if sub.payment_method == "card":
            card = cards_by_id.get(sub.card_id or "")
            pay = f"Cartão: {card.name}" if card else "Cartão"
            card_short = card.name if card else "—"
        else:
            pay = "PIX"
            card_short = "—"
        cat = cat_names.get(sub.category_id, "Assinatura")
        rows.append(
            {
                "kind": "subscription",
                "sort_key": (sub.start_year, sub.start_month, sub.billing_day, 1),
                "day_month": f"{sub.billing_day:02d}/{sub.start_month + 1:02d}",
                "description": sub.description,
                "purchase_type": "subscription",
                "payment_label": pay,
                "card_short": card_short,
                "month_fmt": brl(month_amount),
                "total_fmt": brl(sub.amount_monthly),
                "status": status,
                "category": cat,
                "expense": None,
                "subscription": sub,
            }
        )
    return rows


def _pix_item_kind_rows(
    session: Session,
    month: int,
    year: int,
    period_filter: str,
    include: bool,
) -> list[dict]:
    if not include:
        return []
    cat_names = category_map_by_id(session)
    items = session.exec(select(PixItem)).all()
    rows: list[dict] = []
    cur = mkey(month, year)
    for pix in sorted(items, key=lambda p: (p.start_year, p.start_month, p.description), reverse=True):
        start = mkey(pix.start_month, pix.start_year)
        if _truthy(pix.is_recurring):
            active = cur >= start
            month_amount = float(pix.amount) if active else 0.0
            status = "Recorrente PIX" if active else "Aguardando início"
        else:
            active = pix.start_month == month and pix.start_year == year
            month_amount = float(pix.amount) if active else 0.0
            status = "No mês" if active else f"Em {fmt_month(pix.start_month, pix.start_year)}"
        if period_filter == "month" and not active:
            continue
        rows.append(
            {
                "kind": "pix_item",
                "sort_key": (pix.start_year, pix.start_month, 1, 2),
                "day_month": f"{1:02d}/{pix.start_month + 1:02d}",
                "description": pix.description,
                "purchase_type": "pix_item",
                "payment_label": "PIX",
                "card_short": "—",
                "month_fmt": brl(month_amount),
                "total_fmt": brl(pix.amount),
                "status": status,
                "category": cat_names.get(pix.category_id, "—"),
                "expense": None,
                "subscription": None,
                "pix_item": pix,
            }
        )
    return rows


def merged_expense_table_rows(
    session: Session,
    month: int,
    year: int,
    card_filter: str = "",
    period_filter: str = "month",
    pay_filter: str = "",
) -> tuple[list[dict], dict[str, str], list[Card]]:
    inc_exp, inc_pix, sub_methods = _lancamentos_filter_parts(pay_filter)
    exp_rows = _expense_kind_rows(session, month, year, card_filter, period_filter, inc_exp)
    sub_rows = _subscription_kind_rows(session, month, year, card_filter, period_filter, sub_methods)
    pix_rows = _pix_item_kind_rows(session, month, year, period_filter, inc_pix)
    merged = exp_rows + sub_rows + pix_rows
    merged.sort(key=lambda r: r["sort_key"], reverse=True)
    cards = session.exec(select(Card)).all()
    return merged, {card.id: card.name for card in cards}, cards


def expenses_table_context(request: Request, session: Session) -> dict:
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    pay_filter = normalize_lancamentos_f_pay(request.query_params.get("f_pay", ""))
    card_filter = request.query_params.get("f_card", "")
    if pay_filter in ("pix_sub", "pix_buy", "pix_all"):
        card_filter = ""
    period_filter = request.query_params.get("period", "month")
    if period_filter not in {"month", "all"}:
        period_filter = "month"
    rows, cards_map, cards = merged_expense_table_rows(
        session,
        month,
        year,
        card_filter=card_filter,
        period_filter=period_filter,
        pay_filter=pay_filter,
    )
    ctx = base_context(request, month, year, settings)
    ctx["query"] = expenses_list_query(request, month, year)
    ctx.update(
        {
            "expense_rows": rows,
            "cards_map": cards_map,
            "cards": cards,
            "filter_card": card_filter,
            "filter_period": period_filter,
            "filter_pay": pay_filter,
            "card_filter_disabled": pay_filter in ("pix_sub", "pix_buy", "pix_all"),
        }
    )
    return ctx


@router.get("")
def expenses_page(request: Request, session: Session = Depends(get_session)):
    context = expenses_table_context(request, session)
    context["active"] = "expenses"
    return templates.TemplateResponse(request, "pages/expenses.html", context)


@router.get("/form")
def expense_form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    cards = session.exec(select(Card)).all()
    today = datetime.now(UTC).date().isoformat()
    context = base_context(request, month, year, settings)
    context["query"] = expenses_list_query(request, month, year)
    context.update({"expense": None, "cards": cards, "purchase_date": today})
    context.update(build_category_field(session, wrap_id="category-wrap-expense", default_name="Outros"))
    return templates.TemplateResponse(request, "partials/expense_form.html", context)


@router.get("/form/{expense_id}")
def expense_form_edit(expense_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    cards = session.exec(select(Card)).all()
    expense = session.get(Expense, expense_id)
    purchase_date = ""
    if expense:
        purchase_date = datetime(expense.purchase_year, expense.purchase_month + 1, expense.purchase_day).date().isoformat()
    context = base_context(request, month, year, settings)
    context["query"] = expenses_list_query(request, month, year)
    context.update({"expense": expense, "cards": cards, "purchase_date": purchase_date})
    context.update(
        build_category_field(
            session,
            wrap_id="category-wrap-expense",
            selected_id=expense.category_id if expense else None,
            default_name="Outros",
        )
    )
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
    category_id: str = Form(""),
    session: Session = Depends(get_session),
):
    if exp_type not in {"credit", "debit"}:
        raise HTTPException(status_code=400, detail="Tipo de lançamento inválido.")
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=400, detail="Cartão obrigatório e deve existir.")
    if amount_total <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser maior que zero.")
    try:
        date_obj = datetime.strptime(purchase_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data de compra inválida.") from exc

    try:
        cid = parse_category_id(session, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    expense = session.get(Expense, expense_id) if expense_id else None
    if expense is None:
        expense = Expense(
            type=exp_type,
            card_id=card_id,
            description=description,
            amount_total=amount_total,
            purchase_day=1,
            purchase_month=0,
            purchase_year=2024,
            category_id=cid,
        )
    expense.description = description.strip()
    expense.type = exp_type
    expense.card_id = card_id
    expense.amount_total = float(amount_total)
    expense.installments = 1 if exp_type == "debit" else max(1, int(installments))
    expense.purchase_day = date_obj.day
    expense.purchase_month = date_obj.month - 1
    expense.purchase_year = date_obj.year
    expense.category_id = cid
    expense.updated_at = datetime.now(UTC)
    session.add(expense)
    session.commit()
    context = expenses_table_context(request, session)
    return templates.TemplateResponse(request, "partials/expenses_table.html", context)


@router.delete("/{expense_id}")
def delete_expense(expense_id: str, request: Request, session: Session = Depends(get_session)):
    expense = session.get(Expense, expense_id)
    if expense:
        session.delete(expense)
        session.commit()
    context = expenses_table_context(request, session)
    return templates.TemplateResponse(request, "partials/expenses_table.html", context)
