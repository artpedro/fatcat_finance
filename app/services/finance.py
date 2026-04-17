"""Cycle-aware finance rules.

Everything tied to a card (credit, debit, card subscriptions, maintenance) is
grouped by the card's billing cycle (fatura). A cycle is labeled by its
`cycle_end_month` / `cycle_end_year`: the bill that ends in May is the "May
bill" in every graph, filter and metric. PIX flows with no card can optionally
honour a user-defined `pix_closing_day` to form real cycles as well; when
`pix_closing_day == 0` the cycle collapses to a calendar month.
"""

from __future__ import annotations

import calendar
from datetime import date
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


def _month_last_day(month: int, year: int) -> int:
    _, last = calendar.monthrange(year, month + 1)
    return last


def _prev_month(month: int, year: int) -> tuple[int, int]:
    if month == 0:
        return 11, year - 1
    return month - 1, year


def _next_month(month: int, year: int) -> tuple[int, int]:
    if month == 11:
        return 0, year + 1
    return month + 1, year


def effective_closing_day(closing_day: int, month: int, year: int) -> int:
    """Clamp configured closing_day to the month's real length (handles day 31 vs Feb)."""
    last = _month_last_day(month, year)
    if closing_day <= 0:
        return last
    return min(closing_day, last)


def cycle_end_for_purchase(closing_day: int, day: int, month: int, year: int) -> tuple[int, int]:
    """Cycle the given charge date belongs to, expressed as (end_month, end_year).

    - `closing_day <= 0` means "no cycle": fall back to calendar month.
    - If the charge day is past the effective closing day, the charge rolls into
      the next cycle (which ends in the following month).
    """
    if closing_day <= 0:
        return month, year
    eff = effective_closing_day(closing_day, month, year)
    if day <= eff:
        return month, year
    return _next_month(month, year)


def cycle_bounds(
    closing_day: int, end_month: int, end_year: int
) -> tuple[int, int, int, int, int, int]:
    """(start_day, start_month, start_year, end_day, end_month, end_year) for a cycle."""
    end_day = effective_closing_day(closing_day, end_month, end_year)
    if closing_day <= 0:
        return 1, end_month, end_year, end_day, end_month, end_year
    start_month, start_year = _prev_month(end_month, end_year)
    prev_eff = effective_closing_day(closing_day, start_month, start_year)
    start_day = prev_eff + 1
    last_prev = _month_last_day(start_month, start_year)
    if start_day > last_prev:
        start_day = 1
        start_month, start_year = _next_month(start_month, start_year)
    return start_day, start_month, start_year, end_day, end_month, end_year


def active_cycle_today(closing_day: int, today: date | None = None) -> tuple[int, int]:
    """The cycle containing `today` for a given closing_day."""
    if today is None:
        today = date.today()
    return cycle_end_for_purchase(closing_day, today.day, today.month - 1, today.year)


def cycle_vencimento(due_day: int, end_month: int, end_year: int) -> date:
    """Vencimento date: `due_day` of the month AFTER the cycle end."""
    m, y = _next_month(end_month, end_year)
    last = _month_last_day(m, y)
    d = min(max(1, due_day), last)
    return date(y, m + 1, d)


def billing_start(expense: Expense, card: Card | None) -> tuple[int, int]:
    """Back-compat alias resolving the cycle that holds an expense's first charge."""
    closing = card.closing_day if card else 0
    return cycle_end_for_purchase(closing, expense.purchase_day, expense.purchase_month, expense.purchase_year)


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


def subscription_cycle_hit(
    subscription: Subscription, closing_day: int, end_month: int, end_year: int
) -> bool:
    """Whether a subscription's charge falls into the cycle ending at (end_month, end_year).

    Each month the subscription charges on `billing_day`. That calendar date
    maps to a card cycle via `cycle_end_for_purchase`. We find which calendar
    month would be charged into the target cycle and check subscription
    activity there.
    """
    if closing_day <= 0:
        return is_subscription_active(subscription, end_month, end_year)
    eff = effective_closing_day(closing_day, end_month, end_year)
    if subscription.billing_day <= eff:
        charge_m, charge_y = end_month, end_year
    else:
        charge_m, charge_y = _prev_month(end_month, end_year)
    return is_subscription_active(subscription, charge_m, charge_y)


def subscription_charge_date(
    subscription: Subscription, closing_day: int, end_month: int, end_year: int
) -> tuple[int, int, int]:
    """Calendar (day, month, year) where this subscription would charge into the target cycle."""
    if closing_day <= 0:
        day = min(max(1, subscription.billing_day), _month_last_day(end_month, end_year))
        return day, end_month, end_year
    eff = effective_closing_day(closing_day, end_month, end_year)
    if subscription.billing_day <= eff:
        charge_m, charge_y = end_month, end_year
    else:
        charge_m, charge_y = _prev_month(end_month, end_year)
    day = min(max(1, subscription.billing_day), _month_last_day(charge_m, charge_y))
    return day, charge_m, charge_y


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


def pix_cycle_hit(
    pix: PixItem, pix_closing_day: int, end_month: int, end_year: int
) -> bool:
    """Does a PixItem belong to the PIX cycle ending at (end_month, end_year)?

    PixItem has no explicit charge day. We label one-offs by (start_month,
    start_year) and treat recurring items as active for every cycle at or
    after start - identical semantics regardless of pix_closing_day, since
    without a charge day there is no cross-cycle boundary to resolve.
    """
    target = mkey(end_month, end_year)
    start = mkey(pix.start_month, pix.start_year)
    if _truthy(pix.is_recurring):
        return target >= start
    return target == start


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


def _expense_in_cycle(
    expense: Expense, card: Card | None, end_month: int, end_year: int
) -> tuple[bool, float, int]:
    target = mkey(end_month, end_year)
    closing = card.closing_day if card else 0
    start_m, start_y = cycle_end_for_purchase(
        closing, expense.purchase_day, expense.purchase_month, expense.purchase_year
    )
    start_key = mkey(start_m, start_y)
    if expense.type == "debit":
        hit = start_key == target
        return hit, (expense.amount_total if hit else 0.0), 1 if hit else 0
    end_key = start_key + expense.installments - 1
    if start_key <= target <= end_key:
        inst_num = target - start_key + 1
        return True, expense.amount_total / expense.installments, inst_num
    return False, 0.0, 0


def expenses_for_cycle(
    expenses: Iterable[Expense],
    cards_by_id: dict[str, Card],
    end_month: int,
    end_year: int,
) -> list[dict]:
    """Expenses (credit installments + debits) that land in the given card cycle."""
    rows: list[dict] = []
    for expense in expenses:
        card = cards_by_id.get(expense.card_id)
        active, month_amount, inst_num = _expense_in_cycle(expense, card, end_month, end_year)
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


def expenses_for_month(
    expenses: Iterable[Expense],
    cards_by_id: dict[str, Card],
    month: int,
    year: int,
) -> list[dict]:
    """Back-compat shim: now interprets (month, year) as the cycle-end month."""
    return expenses_for_cycle(expenses, cards_by_id, month, year)


def subscription_costs_by_method(
    items: Iterable[Subscription],
    month: int,
    year: int,
    *,
    card_closing_map: dict[str, int] | None = None,
    pix_closing_day: int = 0,
) -> tuple[list[Subscription], list[Subscription]]:
    """Split subscriptions active in the cycle ending at (month, year) by payment method."""
    card_items: list[Subscription] = []
    pix_items: list[Subscription] = []
    for sub in items:
        if sub.payment_method == "card":
            closing = 0
            if card_closing_map is not None and sub.card_id:
                closing = card_closing_map.get(sub.card_id, 0)
            if subscription_cycle_hit(sub, closing, month, year):
                card_items.append(sub)
        else:
            if subscription_cycle_hit(sub, pix_closing_day, month, year):
                pix_items.append(sub)
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
    """Remaining credit installments not yet billed in cycles <= selected one."""
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


def lines_for_open_cycle(
    *,
    card: Card,
    end_month: int,
    end_year: int,
    expenses: Iterable[Expense],
    subscriptions: Iterable[Subscription],
    category_names: dict[str, str],
) -> list[dict]:
    """Compute bill-line dicts for an open card cycle without persisting them.

    Mirrors the shape of BillCycleLine so routes and templates can render open
    and closed cycles with the same code path.
    """
    lines: list[dict] = []
    cards_by_id = {card.id: card}
    for row in expenses_for_cycle(expenses, cards_by_id, end_month, end_year):
        if row["expense"].card_id != card.id:
            continue
        exp = row["expense"]
        kind = "expense" if exp.type == "credit" else "debit"
        lines.append(
            {
                "kind": kind,
                "source_ref_id": exp.id,
                "description": exp.description,
                "category_name_snapshot": category_names.get(exp.category_id, ""),
                "amount": row["month_amount"],
                "charge_day": exp.purchase_day,
                "charge_month": exp.purchase_month,
                "charge_year": exp.purchase_year,
                "installment_num": row["inst_num"] if exp.type == "credit" else None,
                "installments_total": exp.installments if exp.type == "credit" else None,
                "notes": "",
            }
        )
    for sub in subscriptions:
        if sub.payment_method != "card" or sub.card_id != card.id:
            continue
        if not subscription_cycle_hit(sub, card.closing_day, end_month, end_year):
            continue
        d, m, y = subscription_charge_date(sub, card.closing_day, end_month, end_year)
        lines.append(
            {
                "kind": "subscription",
                "source_ref_id": sub.id,
                "description": sub.description,
                "category_name_snapshot": category_names.get(sub.category_id, ""),
                "amount": sub.amount_monthly,
                "charge_day": d,
                "charge_month": m,
                "charge_year": y,
                "installment_num": None,
                "installments_total": None,
                "notes": "",
            }
        )
    has_spend = sum(line["amount"] for line in lines) > 0
    fee = 0.0
    if card.maintenance_type == "fixed" and card.maintenance_amount > 0:
        fee = card.maintenance_amount
    elif card.maintenance_type == "conditional" and card.maintenance_amount > 0 and has_spend:
        fee = card.maintenance_amount
    if fee > 0:
        end_day = effective_closing_day(card.closing_day, end_month, end_year)
        lines.append(
            {
                "kind": "maintenance",
                "source_ref_id": None,
                "description": "Anuidade / manutenção",
                "category_name_snapshot": "Cartão",
                "amount": fee,
                "charge_day": end_day,
                "charge_month": end_month,
                "charge_year": end_year,
                "installment_num": None,
                "installments_total": None,
                "notes": card.maintenance_type,
            }
        )
    return lines


def lines_for_open_pix_cycle(
    *,
    end_month: int,
    end_year: int,
    pix_closing_day: int,
    pix_items: Iterable[PixItem],
    subscriptions: Iterable[Subscription],
    category_names: dict[str, str],
) -> list[dict]:
    """Compute bill-line dicts for the synthetic PIX cycle."""
    lines: list[dict] = []
    for pix in pix_items:
        if not pix_cycle_hit(pix, pix_closing_day, end_month, end_year):
            continue
        lines.append(
            {
                "kind": "pix",
                "source_ref_id": pix.id,
                "description": pix.description,
                "category_name_snapshot": category_names.get(pix.category_id, ""),
                "amount": pix.amount,
                "charge_day": 1,
                "charge_month": pix.start_month,
                "charge_year": pix.start_year,
                "installment_num": None,
                "installments_total": None,
                "notes": "",
            }
        )
    for sub in subscriptions:
        if sub.payment_method != "pix":
            continue
        if not subscription_cycle_hit(sub, pix_closing_day, end_month, end_year):
            continue
        d, m, y = subscription_charge_date(sub, pix_closing_day, end_month, end_year)
        lines.append(
            {
                "kind": "subscription",
                "source_ref_id": sub.id,
                "description": sub.description,
                "category_name_snapshot": category_names.get(sub.category_id, ""),
                "amount": sub.amount_monthly,
                "charge_day": d,
                "charge_month": m,
                "charge_year": y,
                "installment_num": None,
                "installments_total": None,
                "notes": "",
            }
        )
    return lines


def due_urgency(
    end_month: int, end_year: int, due_day: int, today: date | None = None
) -> tuple[str, str]:
    if today is None:
        today = date.today()
    venc = cycle_vencimento(due_day, end_month, end_year)
    diff = (venc - today).days
    if diff < 0:
        return "overdue", f"Vencido há {abs(diff)}d"
    if diff == 0:
        return "today", "Vence hoje!"
    if diff <= 5:
        return "soon", f"{diff} dias"
    return "normal", f"{diff} dias"
