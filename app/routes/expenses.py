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
    _truthy,
    cycle_end_for_purchase,
    fmt_month,
    mkey,
    subscription_cycle_hit,
)
from app.templates import brl, templates

router = APIRouter(prefix="/expenses", tags=["expenses"])

# f_pay: meio de pagamento cruzado com compras vs assinaturas. Legado: pix -> pix_sub, card -> card_all.
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
    """Query string for cycle-end month/year and lancamentos filters (HTMX links, saves)."""
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
    end_month: int,
    end_year: int,
    card_filter: str,
    period_filter: str,
    include: bool,
) -> list[dict]:
    if not include:
        return []
    cat_names = category_map_by_id(session)
    cards = list(session.exec(select(Card)))
    cards_by_id = {card.id: card for card in cards}
    expenses = list(session.exec(select(Expense)))
    if card_filter:
        expenses = [expense for expense in expenses if expense.card_id == card_filter]
    target_key = mkey(end_month, end_year)
    rows: list[dict] = []
    for expense in sorted(
        expenses, key=lambda e: (e.purchase_year, e.purchase_month, e.purchase_day), reverse=True
    ):
        card = cards_by_id.get(expense.card_id)
        closing = card.closing_day if card else 0
        cem, cey = cycle_end_for_purchase(
            closing, expense.purchase_day, expense.purchase_month, expense.purchase_year
        )
        start_key = mkey(cem, cey)
        active = False
        if expense.type == "debit":
            active = start_key == target_key
            status = "Neste ciclo" if active else f"Ciclo {fmt_month(cem, cey)}"
            month_amount = expense.amount_total if active else 0
        else:
            end_key = start_key + expense.installments - 1
            if target_key < start_key:
                status = "Aguardando ciclo"
                month_amount = 0
            elif target_key > end_key:
                status = "Concluído"
                month_amount = 0
            else:
                current_inst = target_key - start_key + 1
                status = f"{current_inst}/{expense.installments}"
                month_amount = expense.amount_total / expense.installments
                active = True
        if period_filter == "month" and not active:
            continue
        pay = f"Cartão: {card.name}" if card else "—"
        rows.append(
            {
                "kind": "expense",
                "sort_key": (expense.purchase_year, expense.purchase_month, expense.purchase_day, 0),
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
    end_month: int,
    end_year: int,
    card_filter: str,
    period_filter: str,
    methods: frozenset[str] | None,
    pix_closing_day: int,
) -> list[dict]:
    if methods is not None and len(methods) == 0:
        return []
    cat_names = category_map_by_id(session)
    cards_by_id = {c.id: c for c in session.exec(select(Card))}
    subscriptions = list(session.exec(select(Subscription)))
    if methods is not None:
        subscriptions = [s for s in subscriptions if s.payment_method in methods]
    if card_filter:
        subscriptions = [
            s
            for s in subscriptions
            if s.payment_method == "card" and s.card_id == card_filter
        ]
    rows: list[dict] = []
    for sub in sorted(
        subscriptions,
        key=lambda s: (s.start_year, s.start_month, s.billing_day),
        reverse=True,
    ):
        if sub.payment_method == "card":
            closing = cards_by_id.get(sub.card_id or "", Card(name="", closing_day=0, due_day=0)).closing_day
            active = subscription_cycle_hit(sub, closing, end_month, end_year)
        else:
            active = subscription_cycle_hit(sub, pix_closing_day, end_month, end_year)
        if period_filter == "month" and not active:
            continue
        month_amount = sub.amount_monthly if active else 0.0
        if active:
            status = "Ativa neste ciclo"
        elif sub.is_indefinite:
            status = "Fora do ciclo"
        else:
            status = "Fora do período"
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
    end_month: int,
    end_year: int,
    period_filter: str,
    include: bool,
    pix_closing_day: int,
) -> list[dict]:
    if not include:
        return []
    cat_names = category_map_by_id(session)
    items = list(session.exec(select(PixItem)))
    rows: list[dict] = []
    target = mkey(end_month, end_year)
    for pix in sorted(
        items, key=lambda p: (p.start_year, p.start_month, p.description), reverse=True
    ):
        start = mkey(pix.start_month, pix.start_year)
        if _truthy(pix.is_recurring):
            active = target >= start
            month_amount = float(pix.amount) if active else 0.0
            cycle_word = "ciclo" if pix_closing_day > 0 else "mês"
            status = f"Recorrente PIX" if active else "Aguardando início"
            _ = cycle_word
        else:
            active = pix.start_month == end_month and pix.start_year == end_year
            month_amount = float(pix.amount) if active else 0.0
            if active:
                status = "Neste ciclo" if pix_closing_day > 0 else "No mês"
            else:
                status = f"Em {fmt_month(pix.start_month, pix.start_year)}"
        if period_filter == "month" and not active:
            continue
        rows.append(
            {
                "kind": "pix_item",
                "sort_key": (pix.start_year, pix.start_month, 0, 2),
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
    end_month: int,
    end_year: int,
    card_filter: str = "",
    period_filter: str = "month",
    pay_filter: str = "",
    pix_closing_day: int = 0,
) -> tuple[list[dict], dict[str, str], list[Card]]:
    inc_exp, inc_pix, sub_methods = _lancamentos_filter_parts(pay_filter)
    exp_rows = _expense_kind_rows(
        session, end_month, end_year, card_filter, period_filter, inc_exp
    )
    sub_rows = _subscription_kind_rows(
        session,
        end_month,
        end_year,
        card_filter,
        period_filter,
        sub_methods,
        pix_closing_day,
    )
    pix_rows = _pix_item_kind_rows(
        session, end_month, end_year, period_filter, inc_pix, pix_closing_day
    )
    merged = exp_rows + sub_rows + pix_rows
    merged.sort(key=lambda r: r["sort_key"], reverse=True)
    cards = list(session.exec(select(Card)))
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
        pix_closing_day=int(settings.pix_closing_day),
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
    cards = list(session.exec(select(Card)))
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
    cards = list(session.exec(select(Card)))
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
