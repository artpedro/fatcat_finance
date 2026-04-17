from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, col, select

from app.db import get_session
from app.models import BillCycle, Card, Expense, PixItem, Subscription
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.services.bills import (
    lines_for_bill,
    materialize_closed_cycles,
    open_bill_live_total,
    pay_bill,
    unpay_bill,
)
from app.services.finance import (
    MONTHS,
    due_urgency,
    outstanding_for_card,
)
from app.templates import brl, templates

router = APIRouter(prefix="/cards", tags=["cards"])


def _period_fmt(bill: BillCycle) -> str:
    if bill.cycle_start_month == bill.cycle_end_month and bill.cycle_start_year == bill.cycle_end_year:
        return f"{bill.cycle_start_day:02d} - {bill.cycle_end_day:02d} {MONTHS[bill.cycle_end_month]} {bill.cycle_end_year}"
    return (
        f"{bill.cycle_start_day:02d} {MONTHS[bill.cycle_start_month]} "
        f"- {bill.cycle_end_day:02d} {MONTHS[bill.cycle_end_month]} {bill.cycle_end_year}"
    )


def _bill_view(
    session: Session,
    bill: BillCycle,
    *,
    card: Card | None,
    expenses: list[Expense],
    subscriptions: list[Subscription],
    pix_items: list[PixItem],
    category_names: dict[str, str],
    today: date,
) -> dict:
    if bill.status == "open":
        total, lines = open_bill_live_total(
            session,
            bill,
            card=card,
            expenses=expenses,
            subscriptions=subscriptions,
            pix_items=pix_items,
            category_names=category_names,
        )
    else:
        total = float(bill.total_amount)
        lines = lines_for_bill(
            session,
            bill,
            card=card,
            expenses=expenses,
            subscriptions=subscriptions,
            pix_items=pix_items,
            category_names=category_names,
        )
    state, label = due_urgency(
        bill.cycle_end_month, bill.cycle_end_year, bill.due_day_snapshot, today
    )
    return {
        "bill": bill,
        "total": total,
        "total_fmt": brl(total),
        "period_fmt": _period_fmt(bill),
        "cycle_label": f"{MONTHS[bill.cycle_end_month]}/{bill.cycle_end_year}",
        "due_state": state,
        "due_label": label,
        "is_open": bill.status == "open",
        "is_paid": bill.status == "paid",
        "is_unpaid_closed": bill.status == "closed_unpaid",
        "lines": lines,
    }


def _rows(session: Session) -> list[dict]:
    today = date.today()
    materialize_closed_cycles(session, today)

    cards = list(session.exec(select(Card).order_by(col(Card.name))))
    expenses = list(session.exec(select(Expense)))
    subscriptions = list(session.exec(select(Subscription)))
    pix_items = list(session.exec(select(PixItem)))
    from app.category_utils import category_map_by_id

    category_names = category_map_by_id(session)

    used_by_sub = {sub.card_id for sub in subscriptions if sub.payment_method == "card" and sub.card_id}

    rows: list[dict] = []
    for card in cards:
        new_flag = card.id in used_by_sub
        if card.is_used_by_subscriptions != new_flag:
            card.is_used_by_subscriptions = new_flag
            session.add(card)

        bills = list(
            session.exec(
                select(BillCycle)
                .where(BillCycle.scope == "card", BillCycle.card_id == card.id)
                .order_by(col(BillCycle.cycle_end_year), col(BillCycle.cycle_end_month))
            )
        )
        open_bill = next((b for b in bills if b.status == "open"), None)
        unpaid_bills = [b for b in bills if b.status == "closed_unpaid"]
        paid_bills = [b for b in bills if b.status == "paid"]

        actionable: list[dict] = []
        for bill in unpaid_bills + ([open_bill] if open_bill else []):
            actionable.append(
                _bill_view(
                    session,
                    bill,
                    card=card,
                    expenses=expenses,
                    subscriptions=subscriptions,
                    pix_items=pix_items,
                    category_names=category_names,
                    today=today,
                )
            )
        outstanding = outstanding_for_card(
            card,
            expenses,
            open_bill.cycle_end_month if open_bill else today.month - 1,
            open_bill.cycle_end_year if open_bill else today.year,
        )
        rows.append(
            {
                "card": card,
                "actionable": actionable,
                "has_history": any(paid_bills) or any(unpaid_bills),
                "paid_count": len(paid_bills),
                "unpaid_count": len(unpaid_bills),
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
    context.update({"active": "cards", "cards_rows": _rows(session)})
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
    card.updated_at = datetime.now(UTC)
    session.add(card)
    session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)


@router.delete("/{card_id}")
def delete_card(card_id: str, request: Request, session: Session = Depends(get_session)):
    card = session.get(Card, card_id)
    if card:
        for expense in session.exec(select(Expense).where(Expense.card_id == card_id)):
            session.delete(expense)
        for sub in session.exec(select(Subscription).where(Subscription.card_id == card_id)):
            if sub.payment_method == "card":
                session.delete(sub)
        for bill in session.exec(select(BillCycle).where(BillCycle.card_id == card_id)):
            session.delete(bill)
        session.delete(card)
        session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)


@router.post("/{card_id}/bills/{bill_id}/pay")
def pay_card_bill(
    card_id: str,
    bill_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    bill = session.get(BillCycle, bill_id)
    if bill is None or bill.card_id != card_id:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    pay_bill(session, bill_id)
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)


@router.post("/{card_id}/bills/{bill_id}/unpay")
def unpay_card_bill(
    card_id: str,
    bill_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    bill = session.get(BillCycle, bill_id)
    if bill is None or bill.card_id != card_id:
        raise HTTPException(status_code=404, detail="Fatura não encontrada.")
    unpay_bill(session, bill_id)
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"cards_rows": _rows(session)})
    return templates.TemplateResponse(request, "partials/cards_table.html", context)


@router.get("/{card_id}/bills")
def card_bills_history(
    card_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")
    today = date.today()
    expenses = list(session.exec(select(Expense)))
    subscriptions = list(session.exec(select(Subscription)))
    pix_items = list(session.exec(select(PixItem)))
    from app.category_utils import category_map_by_id

    category_names = category_map_by_id(session)
    bills = list(
        session.exec(
            select(BillCycle)
            .where(BillCycle.scope == "card", BillCycle.card_id == card_id)
            .order_by(col(BillCycle.cycle_end_year).desc(), col(BillCycle.cycle_end_month).desc())
        )
    )
    history = [
        _bill_view(
            session,
            bill,
            card=card,
            expenses=expenses,
            subscriptions=subscriptions,
            pix_items=pix_items,
            category_names=category_names,
            today=today,
        )
        for bill in bills
    ]
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"card": card, "bills": history})
    return templates.TemplateResponse(request, "partials/bill_history.html", context)
