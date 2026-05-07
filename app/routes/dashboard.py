from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.category_utils import category_map_by_id
from app.db import get_session
from app.models import (
    Card,
    Expense,
    IncomeSource,
    PixItem,
    Subscription,
)
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.services.bills import card_cycle_view, pix_cycle_view
from app.services.finance import (
    due_urgency,
    income_total_for_month,
)
from app.templates import brl, templates

router = APIRouter(tags=["dashboard"])


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    pix_closing_day = int(settings.pix_closing_day)

    cards = list(session.exec(select(Card)))
    expenses = list(session.exec(select(Expense)))
    subscriptions = list(session.exec(select(Subscription)))
    pix_items = list(session.exec(select(PixItem)))
    incomes = list(session.exec(select(IncomeSource)))
    cat_names = category_map_by_id(session)

    income_total = income_total_for_month(incomes, month, year)

    card_rows: list[dict] = []
    cards_total = 0.0
    cards_overdue = 0.0
    cat_totals: dict[str, float] = {}

    def _fold_lines(lines: list[dict]) -> None:
        for line in lines:
            if line["kind"] == "carryover":
                continue
            cat = line.get("category_name_snapshot") or "Outros"
            cat_totals[cat] = cat_totals.get(cat, 0.0) + float(line["amount"])

    for card in cards:
        view = card_cycle_view(
            session,
            card,
            month,
            year,
            expenses=expenses,
            subscriptions=subscriptions,
            pix_items=pix_items,
            category_names=cat_names,
        )
        cards_total += view["own_total"]
        cards_overdue += view["carryover_total"]
        _fold_lines(view["lines"])
        card_rows.append(
            {
                "card": card,
                "total": view["own_total"],
                "carryover": view["carryover_total"],
                "bill": view["bill"],
                "status": view["status"],
                "is_projected": view["is_projected"],
            }
        )

    pix_adhoc_total = 0.0
    subscription_pix_total = 0.0
    pix_overdue = 0.0
    if pix_closing_day > 0:
        pix_view = pix_cycle_view(
            session,
            month,
            year,
            pix_closing_day=pix_closing_day,
            pix_items=pix_items,
            subscriptions=subscriptions,
            category_names=cat_names,
        )
        pix_overdue = pix_view["carryover_total"]
        for line in pix_view["lines"]:
            if line["kind"] == "carryover":
                continue
            if line["kind"] == "pix":
                pix_adhoc_total += float(line["amount"])
            elif line["kind"] == "subscription":
                subscription_pix_total += float(line["amount"])
        _fold_lines(pix_view["lines"])
    else:
        from app.services.finance import pix_cycle_hit, subscription_cycle_hit

        for pix in pix_items:
            if pix_cycle_hit(pix, 0, month, year):
                pix_adhoc_total += float(pix.amount)
                cat = cat_names.get(pix.category_id, "Outros")
                cat_totals[cat] = cat_totals.get(cat, 0.0) + float(pix.amount)
        for sub in subscriptions:
            if sub.payment_method != "pix":
                continue
            if subscription_cycle_hit(sub, 0, month, year):
                subscription_pix_total += float(sub.amount_monthly)
                cat = cat_names.get(sub.category_id, "Assinatura")
                cat_totals[cat] = cat_totals.get(cat, 0.0) + float(sub.amount_monthly)

    pix_total_out = pix_adhoc_total + subscription_pix_total
    overdue_total = cards_overdue + pix_overdue
    balance = income_total - cards_total - pix_total_out

    chart_card_labels: list[str] = []
    chart_card_values: list[float] = []
    chart_card_colors: list[str] = []
    for row in card_rows:
        if row["total"] <= 0:
            continue
        chart_card_labels.append(row["card"].name)
        chart_card_values.append(round(row["total"], 2))
        chart_card_colors.append(row["card"].color or "#DB8A74")
    if pix_adhoc_total > 0:
        chart_card_labels.append("PIX avulso")
        chart_card_values.append(round(pix_adhoc_total, 2))
        chart_card_colors.append("#E4A840")
    if subscription_pix_total > 0:
        chart_card_labels.append("Assinaturas (PIX)")
        chart_card_values.append(round(subscription_pix_total, 2))
        chart_card_colors.append("#F0C060")

    chart_cat_labels = list(cat_totals.keys())
    chart_cat_values = [round(cat_totals[name], 2) for name in chart_cat_labels]
    palette = ["#DB8A74", "#9B8FD4", "#82C4A8", "#E4A840", "#C4A4D8", "#88B8E0", "#FAC9B8"]
    chart_cat_colors = [palette[idx % len(palette)] for idx, _ in enumerate(chart_cat_labels)]

    sankey_nodes = [{"name": "Receitas", "color": "#82C4A8"}]
    sankey_links: list[dict] = []
    for idx, label in enumerate(chart_cat_labels, start=1):
        sankey_nodes.append({"name": label, "color": chart_cat_colors[(idx - 1) % len(chart_cat_colors)]})
        sankey_links.append({"source": 0, "target": idx, "value": cat_totals[label], "color": sankey_nodes[idx]["color"]})
    if balance > 0:
        sankey_nodes.append({"name": "Saldo", "color": "#82C4A8"})
        sankey_links.append({"source": 0, "target": len(sankey_nodes) - 1, "value": balance, "color": "#82C4A8"})

    today = date.today()
    due_cards: list[dict] = []
    for row in card_rows:
        bill = row.get("bill")
        if bill is not None:
            state, label = due_urgency(
                bill.cycle_end_month,
                bill.cycle_end_year,
                bill.due_day_snapshot,
                today,
            )
        else:
            state, label = due_urgency(month, year, row["card"].due_day, today)
        if bill is None:
            status_label = "Projetada"
        elif bill.status == "paid":
            status_label = "Paga"
        elif bill.status == "closed_unpaid":
            status_label = "Fechada"
        else:
            status_label = "Aberta"
        due_cards.append(
            {
                "name": row["card"].name,
                "day": row["card"].due_day,
                "label": label,
                "state": state,
                "status_label": status_label,
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
                "pix_adhoc": pix_adhoc_total,
                "subscription_pix": subscription_pix_total,
                "pix_total_out": pix_total_out,
                "balance": balance,
                "overdue": overdue_total,
                "income_fmt": brl(income_total),
                "cards_fmt": brl(cards_total),
                "pix_adhoc_fmt": brl(pix_adhoc_total),
                "subscription_pix_fmt": brl(subscription_pix_total),
                "balance_fmt": brl(balance),
                "overdue_fmt": brl(overdue_total),
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
