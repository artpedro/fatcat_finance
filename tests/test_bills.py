from datetime import date

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.category_utils import seed_default_categories
from app.models import (
    AppSettings,
    BillCycle,
    BillCycleLine,
    Card,
    Category,
    Expense,
    PixItem,
    Subscription,
)
from app.services.bills import materialize_closed_cycles, pay_bill, unpay_bill


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(AppSettings())
        s.commit()
        seed_default_categories(s)
        yield s


def _category_id(session: Session, name: str = "Outros") -> str:
    cat = session.exec(select(Category).where(Category.name == name)).first()
    assert cat is not None
    return cat.id


def test_materialize_creates_closed_and_open_cycles(session: Session):
    cid = _category_id(session)
    card = Card(name="Nubank", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    session.add(
        Expense(
            type="credit",
            card_id=card.id,
            description="Notebook",
            amount_total=1200,
            installments=4,
            purchase_day=5,
            purchase_month=0,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 4, 20))
    bills = list(
        session.exec(
            select(BillCycle)
            .where(BillCycle.card_id == card.id)
            .order_by(BillCycle.cycle_end_year, BillCycle.cycle_end_month)
        )
    )
    assert [(b.cycle_end_month, b.cycle_end_year, b.status) for b in bills] == [
        (0, 2026, "closed_unpaid"),
        (1, 2026, "closed_unpaid"),
        (2, 2026, "closed_unpaid"),
        (3, 2026, "closed_unpaid"),
        (4, 2026, "open"),
    ]
    jan = bills[0]
    assert round(jan.total_amount, 2) == 300
    feb = bills[1]
    carry = [line for line in session.exec(select(BillCycleLine).where(BillCycleLine.bill_cycle_id == feb.id)) if line.kind == "carryover"]
    assert carry and round(carry[0].amount, 2) == 300


def test_pay_bill_snapshot_and_unpay(session: Session):
    cid = _category_id(session)
    card = Card(name="Visa", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    session.add(
        Expense(
            type="credit",
            card_id=card.id,
            description="Phone",
            amount_total=200,
            installments=1,
            purchase_day=5,
            purchase_month=3,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 4, 7))
    open_bill = session.exec(
        select(BillCycle).where(BillCycle.card_id == card.id, BillCycle.status == "open")
    ).first()
    assert open_bill is not None
    assert open_bill.cycle_end_month == 3
    pay_bill(session, open_bill.id)
    refreshed = session.get(BillCycle, open_bill.id)
    assert refreshed.status == "paid"
    assert round(refreshed.total_amount, 2) == 200
    lines = list(session.exec(select(BillCycleLine).where(BillCycleLine.bill_cycle_id == refreshed.id)))
    assert any(line.kind == "expense" and round(line.amount, 2) == 200 for line in lines)

    unpay_bill(session, open_bill.id)
    refreshed = session.get(BillCycle, open_bill.id)
    assert refreshed.status == "closed_unpaid"
    assert refreshed.paid_at is None


def test_unpaid_cycle_carries_into_next_open(session: Session):
    cid = _category_id(session)
    card = Card(name="Master", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    session.add(
        Expense(
            type="debit",
            card_id=card.id,
            description="Gas",
            amount_total=80,
            installments=1,
            purchase_day=5,
            purchase_month=2,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 3, 15))
    open_bill = session.exec(
        select(BillCycle).where(BillCycle.card_id == card.id, BillCycle.status == "open")
    ).first()
    carry_lines = list(
        session.exec(
            select(BillCycleLine).where(
                BillCycleLine.bill_cycle_id == open_bill.id,
                BillCycleLine.kind == "carryover",
            )
        )
    )
    assert len(carry_lines) == 1
    assert round(carry_lines[0].amount, 2) == 80

    prev = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == card.id,
            BillCycle.cycle_end_month == 2,
            BillCycle.cycle_end_year == 2026,
        )
    ).first()
    pay_bill(session, prev.id)
    carry_lines = list(
        session.exec(
            select(BillCycleLine).where(
                BillCycleLine.bill_cycle_id == open_bill.id,
                BillCycleLine.kind == "carryover",
            )
        )
    )
    assert carry_lines == []


def test_pix_closing_day_materializes_pix_cycles(session: Session):
    cid = _category_id(session)
    settings = session.exec(select(AppSettings)).first()
    settings.pix_closing_day = 15
    session.add(settings)
    session.add(
        PixItem(
            description="Gym",
            amount=120,
            start_month=0,
            start_year=2026,
            is_recurring=True,
            category_id=cid,
        )
    )
    session.add(
        Subscription(
            description="Spotify PIX",
            amount_monthly=30,
            billing_day=20,
            start_month=0,
            start_year=2026,
            payment_method="pix",
            card_id=None,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 3, 20))
    bills = list(
        session.exec(
            select(BillCycle).where(BillCycle.scope == "pix").order_by(
                BillCycle.cycle_end_year, BillCycle.cycle_end_month
            )
        )
    )
    assert bills
    statuses = {(b.cycle_end_month, b.cycle_end_year): b.status for b in bills}
    assert statuses[(3, 2026)] == "open"
    assert statuses[(0, 2026)] == "closed_unpaid"
    settings.pix_closing_day = 0
    session.add(settings)
    session.commit()


def test_pix_closing_day_zero_does_not_materialize(session: Session):
    cid = _category_id(session)
    session.add(
        PixItem(
            description="Gym",
            amount=120,
            start_month=0,
            start_year=2026,
            is_recurring=True,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 3, 20))
    bills = list(session.exec(select(BillCycle).where(BillCycle.scope == "pix")))
    assert bills == []
