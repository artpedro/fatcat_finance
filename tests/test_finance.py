from datetime import date
from types import SimpleNamespace

from app.models import Card, Expense, IncomeSource, PixItem, Subscription
from app.services.finance import (
    active_cycle_today,
    card_total,
    cycle_bounds,
    cycle_end_for_purchase,
    cycle_vencimento,
    due_urgency,
    effective_closing_day,
    expenses_for_cycle,
    expenses_for_month,
    income_total_for_month,
    is_subscription_active,
    lines_for_open_cycle,
    pix_cycle_hit,
    pix_for_month,
    subscription_charge_date,
    subscription_cycle_hit,
)

_CID = "0123456789abcdef0123456789abcdef"


def test_cycle_end_for_purchase_before_and_after_closing():
    assert cycle_end_for_purchase(10, 12, 0, 2026) == (1, 2026)
    assert cycle_end_for_purchase(10, 10, 0, 2026) == (0, 2026)
    assert cycle_end_for_purchase(10, 12, 11, 2025) == (0, 2026)


def test_cycle_end_closing_zero_is_calendar_month():
    assert cycle_end_for_purchase(0, 1, 3, 2026) == (3, 2026)
    assert cycle_end_for_purchase(0, 31, 3, 2026) == (3, 2026)


def test_effective_closing_day_clamps_to_month_length():
    assert effective_closing_day(31, 1, 2026) == 28
    assert effective_closing_day(31, 1, 2024) == 29
    assert effective_closing_day(10, 0, 2026) == 10


def test_cycle_bounds_regular_month():
    sd, sm, sy, ed, em, ey = cycle_bounds(10, 3, 2026)
    assert (sd, sm, sy) == (11, 2, 2026)
    assert (ed, em, ey) == (10, 3, 2026)


def test_cycle_bounds_handles_closing_31_into_feb():
    sd, sm, sy, ed, em, ey = cycle_bounds(31, 1, 2026)
    assert (ed, em, ey) == (28, 1, 2026)
    assert (sd, sm, sy) == (1, 1, 2026)


def test_active_cycle_today_maps_to_next_cycle_after_closing():
    assert active_cycle_today(10, date(2026, 4, 15)) == (4, 2026)
    assert active_cycle_today(10, date(2026, 4, 5)) == (3, 2026)


def test_cycle_vencimento_falls_in_next_month():
    venc = cycle_vencimento(5, 3, 2026)
    assert venc == date(2026, 5, 5)


def test_due_urgency_buckets():
    today = date(2026, 4, 20)
    # cycle_end (3, 2026) -> Vencimento falls in May
    assert due_urgency(3, 2026, 25, today)[0] == "normal"
    # cycle_end (2, 2026) -> Vencimento falls in April
    assert due_urgency(2, 2026, 25, today)[0] == "soon"
    assert due_urgency(2, 2026, 20, today)[0] == "today"
    assert due_urgency(1, 2026, 25, today)[0] == "overdue"


def test_expenses_for_cycle_credit_installments_and_debit_membership():
    card = Card(name="Visa", closing_day=25, due_day=3)
    credit = Expense(
        type="credit",
        card_id=card.id,
        description="Notebook",
        amount_total=1200,
        installments=4,
        purchase_day=10,
        purchase_month=2,
        purchase_year=2026,
        category_id=_CID,
    )
    debit = Expense(
        type="debit",
        card_id=card.id,
        description="Mercado",
        amount_total=200,
        installments=1,
        purchase_day=5,
        purchase_month=3,
        purchase_year=2026,
        category_id=_CID,
    )
    rows_mar = expenses_for_cycle([credit, debit], {card.id: card}, 2, 2026)
    rows_apr = expenses_for_cycle([credit, debit], {card.id: card}, 3, 2026)
    assert any(r["expense"].id == credit.id and round(r["month_amount"], 2) == 300 for r in rows_mar)
    assert any(r["expense"].id == debit.id and round(r["month_amount"], 2) == 200 for r in rows_apr)
    assert not any(r["expense"].id == debit.id for r in rows_mar)


def test_expenses_for_month_is_back_compat_shim():
    card = Card(name="Visa", closing_day=10, due_day=15)
    exp = Expense(
        type="credit",
        card_id=card.id,
        description="X",
        amount_total=100,
        installments=1,
        purchase_day=12,
        purchase_month=0,
        purchase_year=2026,
        category_id=_CID,
    )
    rows = expenses_for_month([exp], {card.id: card}, 1, 2026)
    assert len(rows) == 1


def test_subscription_cycle_hit_card_rolls_with_closing_day():
    card = Card(name="Visa", closing_day=10, due_day=15)
    sub = Subscription(
        description="Streaming",
        amount_monthly=30,
        billing_day=15,
        start_month=0,
        start_year=2026,
        payment_method="card",
        card_id=card.id,
        category_id=_CID,
    )
    assert subscription_cycle_hit(sub, card.closing_day, 1, 2026)
    assert not subscription_cycle_hit(sub, card.closing_day, 0, 2026)


def test_subscription_charge_date_before_closing():
    sub = Subscription(
        description="Music",
        amount_monthly=20,
        billing_day=5,
        start_month=0,
        start_year=2026,
        payment_method="card",
        card_id="c1",
        category_id=_CID,
    )
    d, m, y = subscription_charge_date(sub, 10, 3, 2026)
    assert (d, m, y) == (5, 3, 2026)


def test_lines_for_open_cycle_credits_subs_and_conditional_fee():
    card = Card(
        name="Black",
        closing_day=10,
        due_day=20,
        maintenance_type="conditional",
        maintenance_amount=15,
    )
    credit = Expense(
        type="credit",
        card_id=card.id,
        description="Phone",
        amount_total=400,
        installments=2,
        purchase_day=3,
        purchase_month=3,
        purchase_year=2026,
        category_id=_CID,
    )
    sub = Subscription(
        description="Music",
        amount_monthly=20,
        billing_day=3,
        start_month=1,
        start_year=2026,
        payment_method="card",
        card_id=card.id,
        category_id=_CID,
    )
    lines = lines_for_open_cycle(
        card=card,
        end_month=3,
        end_year=2026,
        expenses=[credit],
        subscriptions=[sub],
        category_names={_CID: "Tech"},
    )
    kinds = {line["kind"] for line in lines}
    assert kinds == {"expense", "subscription", "maintenance"}
    total = sum(line["amount"] for line in lines)
    assert round(total, 2) == 200 + 20 + 15


def test_card_total_with_subscription_and_conditional_fee():
    card = Card(name="Master", closing_day=10, due_day=20, maintenance_type="conditional", maintenance_amount=10)
    expense = Expense(
        type="credit",
        card_id=card.id,
        description="Phone",
        amount_total=100,
        installments=1,
        purchase_day=1,
        purchase_month=5,
        purchase_year=2026,
        category_id=_CID,
    )
    rows = expenses_for_cycle([expense], {card.id: card}, 5, 2026)
    sub = Subscription(
        description="Music",
        amount_monthly=20,
        billing_day=3,
        start_month=1,
        start_year=2026,
        payment_method="card",
        card_id=card.id,
        category_id=_CID,
    )
    total = card_total(card, rows, [sub])
    assert round(total, 2) == 130.00


def test_income_and_subscription_month_activity():
    income = IncomeSource(name="Salary", amount=2500, start_month=0, start_year=2026, is_recurring=True)
    bonus = IncomeSource(name="Bonus", amount=1000, start_month=2, start_year=2026, is_recurring=False)
    assert income_total_for_month([income, bonus], 2, 2026) == 3500
    assert income_total_for_month([income, bonus], 3, 2026) == 2500

    sub = Subscription(
        description="Streaming",
        amount_monthly=39.9,
        billing_day=5,
        start_month=0,
        start_year=2026,
        is_indefinite=False,
        duration_months=3,
        payment_method="pix",
        category_id=_CID,
    )
    assert is_subscription_active(sub, 1, 2026)
    assert not is_subscription_active(sub, 4, 2026)


def test_pix_for_month_and_cycle_hit():
    recurring = PixItem(
        description="Gym", amount=120, start_month=1, start_year=2026, is_recurring=True, category_id=_CID
    )
    one_off = PixItem(
        description="Gift", amount=300, start_month=2, start_year=2026, is_recurring=False, category_id=_CID
    )
    mar = pix_for_month([recurring, one_off], 2, 2026)
    apr = pix_for_month([recurring, one_off], 3, 2026)
    assert {x.description for x in mar} == {"Gym", "Gift"}
    assert {x.description for x in apr} == {"Gym"}
    assert pix_cycle_hit(recurring, 5, 3, 2026) is True
    assert pix_cycle_hit(one_off, 5, 3, 2026) is False


def test_pix_one_off_with_sqlite_style_int_zero():
    one_off = SimpleNamespace(start_month=2, start_year=2026, is_recurring=0, description="x")
    assert len(pix_for_month([one_off], 2, 2026)) == 1
    assert len(pix_for_month([one_off], 3, 2026)) == 0


def test_pix_recurring_with_sqlite_style_int_one():
    sub = SimpleNamespace(start_month=1, start_year=2026, is_recurring=1, description="x")
    assert len(pix_for_month([sub], 6, 2026)) == 1
