from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import Card, Expense, IncomeSource, PixItem, Subscription
from app.routes.common import base_context, current_period, get_settings
from app.services.finance import (
    card_total,
    due_urgency,
    expenses_for_month,
    income_total_for_month,
    pix_for_month,
    subscription_costs_by_method,
)
from app.templates import brl, templates

router = APIRouter(tags=["dashboard"])


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    settings.selected_month = month
    settings.selected_year = year
    session.add(settings)
    session.commit()

    cards = session.exec(select(Card)).all()
    expenses = session.exec(select(Expense)).all()
    subscriptions = session.exec(select(Subscription)).all()
    pix_items = session.exec(select(PixItem)).all()
    incomes = session.exec(select(IncomeSource)).all()

    cards_by_id = {card.id: card for card in cards}
    month_exp = expenses_for_month(expenses, cards_by_id, month, year)
    card_subs, pix_subs = subscription_costs_by_method(subscriptions, month, year)
    month_pix = pix_for_month(pix_items, month, year)
    income_total = income_total_for_month(incomes, month, year)

    card_rows: list[dict] = []
    cards_total = 0.0
    for card in cards:
        total = card_total(card, month_exp, card_subs)
        cards_total += total
        card_rows.append({"card": card, "total": total})

    pix_total = sum(item.amount for item in month_pix) + sum(item.amount_monthly for item in pix_subs)
    balance = income_total - cards_total - pix_total

    chart_card_labels: list[str] = []
    chart_card_values: list[float] = []
    chart_card_colors: list[str] = []
    for row in card_rows:
        if row["total"] <= 0:
            continue
        chart_card_labels.append(row["card"].name)
        chart_card_values.append(round(row["total"], 2))
        chart_card_colors.append(row["card"].color or "#DB8A74")
    if pix_total > 0:
        chart_card_labels.append("PIX & Assinaturas")
        chart_card_values.append(round(pix_total, 2))
        chart_card_colors.append("#E4A840")

    cat_totals: dict[str, float] = {}
    for row in month_exp:
        cat = row["expense"].category or "Outros"
        cat_totals[cat] = cat_totals.get(cat, 0.0) + row["month_amount"]
    for item in month_pix:
        cat_totals[item.category] = cat_totals.get(item.category, 0.0) + item.amount
    for sub in pix_subs:
        cat = sub.pix_category or "Assinatura"
        cat_totals[cat] = cat_totals.get(cat, 0.0) + sub.amount_monthly
    chart_cat_labels = list(cat_totals.keys())
    chart_cat_values = [round(cat_totals[name], 2) for name in chart_cat_labels]
    chart_cat_colors = ["#DB8A74", "#9B8FD4", "#82C4A8", "#E4A840", "#C4A4D8", "#88B8E0", "#FAC9B8"]
    chart_cat_colors = [chart_cat_colors[idx % len(chart_cat_colors)] for idx, _ in enumerate(chart_cat_labels)]

    sankey_nodes = [{"name": "Receitas", "color": "#82C4A8"}]
    sankey_links: list[dict] = []
    for idx, label in enumerate(chart_cat_labels, start=1):
        sankey_nodes.append({"name": label, "color": chart_cat_colors[(idx - 1) % len(chart_cat_colors)]})
        sankey_links.append({"source": 0, "target": idx, "value": cat_totals[label], "color": sankey_nodes[idx]["color"]})
    if balance > 0:
        sankey_nodes.append({"name": "Saldo", "color": "#82C4A8"})
        sankey_links.append({"source": 0, "target": len(sankey_nodes) - 1, "value": balance, "color": "#82C4A8"})

    due_cards = []
    for row in card_rows:
        state, label = due_urgency(month, year, row["card"].due_day)
        due_cards.append(
            {
                "name": row["card"].name,
                "day": row["card"].due_day,
                "label": label,
                "state": state,
                "amount_fmt": brl(row["total"]),
            }
        )

    context = base_context(request, month, year, settings)
    context.update(
        {
            "active": "dashboard",
            "metrics": {
                "income": income_total,
                "cards": cards_total,
                "pix": pix_total,
                "balance": balance,
                "income_fmt": brl(income_total),
                "cards_fmt": brl(cards_total),
                "pix_fmt": brl(pix_total),
                "balance_fmt": brl(balance),
            },
            "chart_card": {"labels": chart_card_labels, "values": chart_card_values, "colors": chart_card_colors},
            "chart_cat": {"labels": chart_cat_labels, "values": chart_cat_values, "colors": chart_cat_colors},
            "sankey": {"nodes": sankey_nodes, "links": sankey_links},
            "breakdown": [
                {
                    "name": row["card"].name,
                    "total_fmt": brl(row["total"]),
                    "pct": round((row["total"] / income_total) * 100, 1) if income_total > 0 else 0,
                    "color": row["card"].color or "#DB8A74",
                }
                for row in card_rows
                if row["total"] > 0
            ],
            "due_cards": due_cards,
        }
    )
    return templates.TemplateResponse(request, "pages/dashboard.html", context)

