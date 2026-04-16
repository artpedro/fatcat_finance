from __future__ import annotations

from datetime import datetime
from typing import Iterable

from app.models import Card, Expense, IncomeSource, PixItem, Subscription

MONTHS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
MONTHS_FULL = [
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
]


def mkey(month: int, year: int) -> int:
    return year * 12 + month


def fmt_month(month: int, year: int) -> str:
    return f"{MONTHS[month]}/{year}"


def billing_start(expense: Expense, card: Card | None) -> tuple[int, int]:
    bm, by = expense.purchase_month, expense.purchase_year
    if card is None or card.closing_day <= 0:
        return bm, by
    if expense.purchase_day > card.closing_day:
        bm += 1
        if bm > 11:
            bm = 0
            by += 1
    return bm, by


def is_income_active(source: IncomeSource, month: int, year: int) -> bool:
    start = mkey(source.start_month, source.start_year)
    current = mkey(month, year)
    if current < start:
        return False
    if not _truthy(source.is_recurring):
        return current == start
    if source.end_month is not None and source.end_year is not None:
        return current <= mkey(source.end_month, source.end_year)
    return True


def income_total_for_month(sources: Iterable[IncomeSource], month: int, year: int) -> float:
    return sum(s.amount for s in sources if is_income_active(s, month, year))


def is_subscription_active(subscription: Subscription, month: int, year: int) -> bool:
    current = mkey(month, year)
    start = mkey(subscription.start_month, subscription.start_year)
    if current < start:
        return False
    if _truthy(subscription.is_indefinite):
        return True
    if subscription.duration_months:
        return current <= start + subscription.duration_months - 1
    if subscription.end_month is not None and subscription.end_year is not None:
        return current <= mkey(subscription.end_month, subscription.end_year)
    return False


def subscriptions_for_month(items: Iterable[Subscription], month: int, year: int) -> list[Subscription]:
    return [s for s in items if is_subscription_active(s, month, year)]


def _truthy(value: object) -> bool:
    """Normalize bool-like values from SQLite/drivers (0/1, strings, None)."""
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def pix_for_month(items: Iterable[PixItem], month: int, year: int) -> list[PixItem]:
    current = mkey(month, year)
    result: list[PixItem] = []
    for pix in items:
        start = mkey(pix.start_month, pix.start_year)
        if _truthy(pix.is_recurring):
            if current >= start:
                result.append(pix)
        elif current == start:
            result.append(pix)
    return result


def _expense_months(expense: Expense, card: Card | None, selected_month: int, selected_year: int) -> tuple[bool, float, int]:
    selected_key = mkey(selected_month, selected_year)
    if expense.type == "debit":
        own_month = expense.purchase_month == selected_month and expense.purchase_year == selected_year
        return own_month, expense.amount_total, 1

    start_month, start_year = billing_start(expense, card)
    start_key = mkey(start_month, start_year)
    end_key = start_key + expense.installments - 1
    if start_key <= selected_key <= end_key:
        inst_num = selected_key - start_key + 1
        return True, expense.amount_total / expense.installments, inst_num
    return False, 0.0, 0


def expenses_for_month(
    expenses: Iterable[Expense],
    cards_by_id: dict[str, Card],
    month: int,
    year: int,
) -> list[dict]:
    rows: list[dict] = []
    for expense in expenses:
        card = cards_by_id.get(expense.card_id)
        active, month_amount, inst_num = _expense_months(expense, card, month, year)
        if not active:
            continue
        bm, by = billing_start(expense, card)
        rows.append(
            {
                "expense": expense,
                "month_amount": month_amount,
                "inst_num": inst_num,
                "billing_month": bm,
                "billing_year": by,
            }
        )
    return rows


def subscription_costs_by_method(items: Iterable[Subscription], month: int, year: int) -> tuple[list[Subscription], list[Subscription]]:
    active = subscriptions_for_month(items, month, year)
    card_items = [s for s in active if s.payment_method == "card"]
    pix_items = [s for s in active if s.payment_method == "pix"]
    return card_items, pix_items


def card_total(
    card: Card,
    month_expenses: Iterable[dict],
    card_subscriptions: Iterable[Subscription],
) -> float:
    exp_total = sum(row["month_amount"] for row in month_expenses if row["expense"].card_id == card.id)
    sub_total = sum(sub.amount_monthly for sub in card_subscriptions if sub.card_id == card.id)
    fee = 0.0
    if card.maintenance_type == "fixed" and card.maintenance_amount > 0:
        fee = card.maintenance_amount
    elif card.maintenance_type == "conditional" and card.maintenance_amount > 0:
        has_spend = exp_total + sub_total > 0
        fee = card.maintenance_amount if has_spend else 0.0
    return exp_total + sub_total + fee


def outstanding_for_card(
    card: Card,
    expenses: Iterable[Expense],
    selected_month: int,
    selected_year: int,
) -> float:
    current = mkey(selected_month, selected_year)
    total = 0.0
    for expense in expenses:
        if expense.card_id != card.id or expense.type != "credit":
            continue
        start_m, start_y = billing_start(expense, card)
        start = mkey(start_m, start_y)
        end = start + expense.installments - 1
        if end >= current:
            remaining = max(0, end - current + 1)
            total += (expense.amount_total / expense.installments) * remaining
    return total


def due_urgency(month: int, year: int, due_day: int) -> tuple[str, str]:
    today = datetime.utcnow().date()
    due_date = datetime(year, month + 1, max(1, due_day)).date()
    diff = (due_date - today).days
    if diff < 0:
        return "overdue", f"Vencido há {abs(diff)}d"
    if diff == 0:
        return "today", "Vence hoje!"
    if diff <= 5:
        return "soon", f"{diff} dias"
    return "normal", f"{diff} dias"

