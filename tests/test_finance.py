from types import SimpleNamespace

from app.models import Card, Expense, IncomeSource, PixItem, Subscription

_CID = "0123456789abcdef0123456789abcdef"
from app.services.finance import (
    billing_start,
    card_total,
    expenses_for_month,
    income_total_for_month,
    is_subscription_active,
    pix_for_month,
)


def test_billing_rolls_after_closing_day():
    card = Card(name="Nubank", closing_day=10, due_day=15)
    expense = Expense(
        type="credit",
        card_id=card.id,
        description="Compra",
        amount_total=300,
        installments=3,
        purchase_day=12,
        purchase_month=0,
        purchase_year=2026,
        category_id=_CID,
    )
    bm, by = billing_start(expense, card)
    assert (bm, by) == (1, 2026)


def test_credit_installment_projection_and_debit_month_rule():
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
    rows_mar = expenses_for_month([credit, debit], {card.id: card}, 2, 2026)
    rows_apr = expenses_for_month([credit, debit], {card.id: card}, 3, 2026)
    assert any(r["expense"].id == credit.id and round(r["month_amount"], 2) == 300 for r in rows_mar)
    assert any(r["expense"].id == debit.id and round(r["month_amount"], 2) == 200 for r in rows_apr)
    assert not any(r["expense"].id == debit.id for r in rows_mar)


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


def test_pix_recurrence_rule():
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


def test_pix_one_off_with_sqlite_style_int_zero():
    """SQLite may surface 0/1 integers; one-off must not repeat after start month."""
    one_off = SimpleNamespace(start_month=2, start_year=2026, is_recurring=0, description="x")
    assert len(pix_for_month([one_off], 2, 2026)) == 1
    assert len(pix_for_month([one_off], 3, 2026)) == 0


def test_pix_recurring_with_sqlite_style_int_one():
    sub = SimpleNamespace(start_month=1, start_year=2026, is_recurring=1, description="x")
    assert len(pix_for_month([sub], 6, 2026)) == 1


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
    rows = expenses_for_month([expense], {card.id: card}, 5, 2026)
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
