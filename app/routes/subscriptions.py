from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.category_utils import parse_category_id
from app.db import get_session
from app.form_dates import month_year_to_date_iso, parse_iso_date_to_month_year
from app.models import Card, Subscription
from app.routes.categories import build_category_field
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.routes.expenses import expenses_list_query
from app.services.finance import fmt_month, is_subscription_active
from app.templates import brl, templates

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _subscription_form_context(
    request: Request,
    session: Session,
    sub: Subscription | None,
    start_date_iso: str,
    end_date_iso: str,
) -> dict:
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    cards = session.exec(select(Card)).all()
    ctx = base_context(request, month, year, settings)
    return_partial = request.query_params.get("return_partial", "")
    if return_partial == "expenses":
        form_hx_target = "#expenses-table-wrap"
        form_cancel_target = "#expense-form-wrap"
    else:
        form_hx_target = "#subscriptions-table-wrap"
        form_cancel_target = "#sub-form-wrap"
    ctx.update(
        {
            "sub": sub,
            "cards": cards,
            "start_date_iso": start_date_iso,
            "end_date_iso": end_date_iso,
            "return_partial": return_partial,
            "form_hx_target": form_hx_target,
            "form_cancel_target": form_cancel_target,
        }
    )
    ctx.update(
        build_category_field(
            session,
            wrap_id="category-wrap-subscription",
            selected_id=sub.category_id if sub else None,
            default_name="Assinatura",
        )
    )
    if return_partial == "expenses":
        ctx["query"] = expenses_list_query(request, month, year)
    return ctx


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
    month, year = resolve_and_sync_period(request, session, settings)
    rows, cards = _rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"active": "subscriptions", "subscriptions_rows": rows, "cards": cards})
    return templates.TemplateResponse(request, "pages/subscriptions.html", context)


@router.get("/form")
def form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    start_date_iso = month_year_to_date_iso(year, month, 1)
    context = _subscription_form_context(request, session, None, start_date_iso, "")
    return templates.TemplateResponse(request, "partials/subscription_form.html", context)


@router.get("/form/{sub_id}")
def form_edit(sub_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    sub = session.get(Subscription, sub_id)
    start_date_iso = month_year_to_date_iso(year, month, 1)
    end_date_iso = ""
    if sub:
        start_date_iso = month_year_to_date_iso(sub.start_year, sub.start_month, 1)
        if sub.end_month is not None and sub.end_year is not None:
            end_date_iso = month_year_to_date_iso(sub.end_year, sub.end_month, 1)
    context = _subscription_form_context(request, session, sub, start_date_iso, end_date_iso)
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
    duration_months: str = Form(""),
    category_id: str = Form(""),
    return_partial: str = Form(""),
    session: Session = Depends(get_session),
):
    if payment_method not in {"card", "pix"}:
        raise HTTPException(status_code=400, detail="Método de pagamento inválido.")
    if amount_monthly <= 0:
        raise HTTPException(status_code=400, detail="Valor mensal deve ser maior que zero.")
    if billing_day < 1 or billing_day > 31:
        raise HTTPException(status_code=400, detail="Dia de cobrança inválido.")
    if payment_method == "card":
        if not card_id:
            raise HTTPException(status_code=400, detail="Assinatura em cartão exige cartão vinculado.")
        if session.get(Card, card_id) is None:
            raise HTTPException(status_code=400, detail="Cartão vinculado não existe.")

    try:
        cid = parse_category_id(session, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sub = session.get(Subscription, sub_id) if sub_id else None
    if sub is None:
        sub = Subscription(
            description=description,
            amount_monthly=amount_monthly,
            billing_day=billing_day,
            start_month=0,
            start_year=2024,
            category_id=cid,
        )
    try:
        sm_start, sy_start = parse_iso_date_to_month_year(start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data de início inválida.") from exc
    sub.description = description.strip()
    sub.amount_monthly = float(amount_monthly)
    sub.billing_day = int(billing_day)
    sub.payment_method = payment_method
    sub.card_id = card_id or None
    sub.start_year = sy_start
    sub.start_month = sm_start

    end_raw = (end or "").strip()
    dur_raw = (duration_months or "").strip()
    dur: int | None = None
    if dur_raw:
        try:
            dur = int(dur_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Duração em meses inválida.") from exc
        if dur < 1:
            raise HTTPException(status_code=400, detail="Duração deve ser pelo menos 1 mês.")

    has_end = bool(end_raw)
    has_dur = dur is not None

    if has_end and has_dur:
        raise HTTPException(
            status_code=400,
            detail="Preencha apenas a data final ou apenas a duração em meses.",
        )

    if not has_end and not has_dur:
        sub.is_indefinite = True
        sub.duration_months = None
        sub.end_year = None
        sub.end_month = None
    else:
        sub.is_indefinite = False
        if has_dur:
            sub.duration_months = dur
            sub.end_year = None
            sub.end_month = None
        else:
            try:
                em, ey = parse_iso_date_to_month_year(end_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Data final inválida.") from exc
            sub.end_month = em
            sub.end_year = ey
            sub.duration_months = None
    if payment_method == "pix":
        sub.card_id = None
    sub.category_id = cid
    sub.updated_at = datetime.now(UTC)
    session.add(sub)
    session.commit()

    if return_partial == "expenses":
        from app.routes.expenses import expenses_table_context

        context = expenses_table_context(request, session)
        return templates.TemplateResponse(request, "partials/expenses_table.html", context)

    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
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
    if request.query_params.get("partial") == "expenses":
        from app.routes.expenses import expenses_table_context

        context = expenses_table_context(request, session)
        return templates.TemplateResponse(request, "partials/expenses_table.html", context)

    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    rows, _cards = _rows(session, month, year)
    context = base_context(request, month, year, settings)
    context.update({"subscriptions_rows": rows})
    return templates.TemplateResponse(request, "partials/subscriptions_table.html", context)

