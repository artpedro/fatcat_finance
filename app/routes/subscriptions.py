from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Card, Subscription
from app.routes.common import base_context, current_period, get_settings
from app.services.finance import fmt_month, is_subscription_active
from app.templates import brl, templates

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _rows(session: Session, month: int, year: int) -> tuple[list[dict], list[Card]]:
    cards = session.exec(select(Card)).all()
    cards_map = {card.id: card.name for card in cards}
    subscriptions = session.exec(select(Subscription)).all()
    rows: list[dict] = []
    for sub in subscriptions:
        end_label = "Sem fim"
        if sub.duration_months:
            end_label = f"{sub.duration_months} meses"
        elif sub.end_month is not None and sub.end_year is not None:
            end_label = fmt_month(sub.end_month, sub.end_year)
        period = f"{fmt_month(sub.start_month, sub.start_year)} até {end_label}"
        pay_label = f"Cartão: {cards_map.get(sub.card_id, '—')}" if sub.payment_method == "card" else "PIX"
        rows.append(
            {
                "sub": sub,
                "active": is_subscription_active(sub, month, year),
                "period": period,
                "pay_label": pay_label,
                "amount_fmt": brl(sub.amount_monthly),
            }
        )
    rows.sort(key=lambda row: (row["sub"].start_year, row["sub"].start_month), reverse=True)
    return rows, cards


@router.get("")
def page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, cards = _rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"active": "subscriptions", "subscriptions_rows": rows, "cards": cards})
    return templates.TemplateResponse(request, "pages/subscriptions.html", context)


@router.get("/form")
def form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    cards = session.exec(select(Card)).all()
    context = base_context(request, month, year, settings)
    context.update({"sub": None, "cards": cards, "start_val": f"{year}-{month+1:02d}", "end_val": ""})
    return templates.TemplateResponse(request, "partials/subscription_form.html", context)


@router.get("/form/{sub_id}")
def form_edit(sub_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    cards = session.exec(select(Card)).all()
    sub = session.get(Subscription, sub_id)
    start_val = ""
    end_val = ""
    if sub:
        start_val = f"{sub.start_year}-{sub.start_month + 1:02d}"
        if sub.end_month is not None and sub.end_year is not None:
            end_val = f"{sub.end_year}-{sub.end_month + 1:02d}"
    context = base_context(request, month, year, settings)
    context.update({"sub": sub, "cards": cards, "start_val": start_val, "end_val": end_val})
    return templates.TemplateResponse(request, "partials/subscription_form.html", context)


@router.get("/form-clear", response_class=HTMLResponse)
def clear_form() -> str:
    return ""


@router.post("/save")
def save(
    request: Request,
    sub_id: str = Form(""),
    description: str = Form(...),
    amount_monthly: float = Form(...),
    billing_day: int = Form(5),
    payment_method: str = Form("card"),
    card_id: str = Form(""),
    start: str = Form(...),
    end: str = Form(""),
    duration_months: int | None = Form(None),
    is_indefinite: str = Form("true"),
    pix_category: str = Form("Assinatura"),
    session: Session = Depends(get_session),
):
    sub = session.get(Subscription, sub_id) if sub_id else None
    if sub is None:
        sub = Subscription(description=description, amount_monthly=amount_monthly, billing_day=billing_day, start_month=0, start_year=2024)
    sy, sm = start.split("-")
    sub.description = description.strip()
    sub.amount_monthly = float(amount_monthly)
    sub.billing_day = int(billing_day)
    sub.payment_method = payment_method
    sub.card_id = card_id or None
    sub.start_year = int(sy)
    sub.start_month = int(sm) - 1
    sub.is_indefinite = is_indefinite.lower() == "true"
    sub.duration_months = int(duration_months) if duration_months else None
    if end:
        ey, em = end.split("-")
        sub.end_year = int(ey)
        sub.end_month = int(em) - 1
    else:
        sub.end_year = None
        sub.end_month = None
    if sub.is_indefinite:
        sub.duration_months = None
        sub.end_year = None
        sub.end_month = None
    sub.pix_category = pix_category
    sub.updated_at = datetime.utcnow()
    session.add(sub)
    session.commit()

    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, _cards = _rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"subscriptions_rows": rows})
    return templates.TemplateResponse(request, "partials/subscriptions_table.html", context)


@router.delete("/{sub_id}")
def delete(sub_id: str, request: Request, session: Session = Depends(get_session)):
    sub = session.get(Subscription, sub_id)
    if sub:
        session.delete(sub)
        session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
    rows, _cards = _rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"subscriptions_rows": rows})
    return templates.TemplateResponse(request, "partials/subscriptions_table.html", context)

