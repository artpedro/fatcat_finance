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
    for closed_bill in bills[:-1]:
        assert round(closed_bill.total_amount, 2) == 300, (
            f"Closed bill {closed_bill.cycle_end_month}/{closed_bill.cycle_end_year} should "
            "store ONLY its own period (no carryover)."
        )
        carry = [
            line
            for line in session.exec(
                select(BillCycleLine).where(BillCycleLine.bill_cycle_id == closed_bill.id)
            )
            if line.kind == "carryover"
        ]
        assert carry == [], "Closed bills must never store carryover lines."

    open_bill = bills[-1]
    open_carry = sorted(
        (
            line
            for line in session.exec(
                select(BillCycleLine).where(BillCycleLine.bill_cycle_id == open_bill.id)
            )
            if line.kind == "carryover"
        ),
        key=lambda r: (r.charge_year, r.charge_month, r.amount),
    )
    assert len(open_carry) == 4, "Open bill must carry one line per prior unpaid bill."
    assert all(round(line.amount, 2) == 300 for line in open_carry)


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
    materialize_closed_cycles(session, date(2026, 5, 15))
    closed_bill = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == card.id,
            BillCycle.cycle_end_month == 3,
            BillCycle.cycle_end_year == 2026,
        )
    ).first()
    assert closed_bill is not None
    assert closed_bill.status == "closed_unpaid"
    assert round(closed_bill.total_amount, 2) == 200

    pay_bill(session, closed_bill.id)
    refreshed = session.get(BillCycle, closed_bill.id)
    assert refreshed.status == "paid"
    assert round(refreshed.total_amount, 2) == 200
    lines = list(
        session.exec(select(BillCycleLine).where(BillCycleLine.bill_cycle_id == refreshed.id))
    )
    assert any(line.kind == "expense" and round(line.amount, 2) == 200 for line in lines)
    assert not any(line.kind == "carryover" for line in lines)

    unpay_bill(session, closed_bill.id)
    refreshed = session.get(BillCycle, closed_bill.id)
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
    pay_bill(session, prev.id, today=date(2026, 3, 15))
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


def test_two_unpaid_cycles_produce_two_carryover_lines(session: Session):
    """Each closed_unpaid bill becomes a DISTINCT carryover line on the open cycle."""
    cid = _category_id(session)
    card = Card(name="Master", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    session.add(
        Expense(
            type="debit",
            card_id=card.id,
            description="Jan",
            amount_total=100,
            installments=1,
            purchase_day=5,
            purchase_month=0,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.add(
        Expense(
            type="debit",
            card_id=card.id,
            description="Feb",
            amount_total=200,
            installments=1,
            purchase_day=5,
            purchase_month=1,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 2, 15))
    open_bill = session.exec(
        select(BillCycle).where(BillCycle.card_id == card.id, BillCycle.status == "open")
    ).first()
    carry = sorted(
        (
            line
            for line in session.exec(
                select(BillCycleLine).where(
                    BillCycleLine.bill_cycle_id == open_bill.id,
                    BillCycleLine.kind == "carryover",
                )
            )
        ),
        key=lambda r: r.amount,
    )
    assert len(carry) == 2, "Both prior unpaid cycles must surface as separate carryover lines."
    assert [round(c.amount, 2) for c in carry] == [100, 200]


def test_paying_oldest_unpaid_does_not_invalidate_other_snapshots(session: Session):
    """Paying an old unpaid bill must NOT touch the next closed bill's lines."""
    cid = _category_id(session)
    card = Card(name="Master", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    session.add(
        Expense(
            type="debit",
            card_id=card.id,
            description="Jan",
            amount_total=100,
            installments=1,
            purchase_day=5,
            purchase_month=0,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.add(
        Expense(
            type="debit",
            card_id=card.id,
            description="Feb",
            amount_total=200,
            installments=1,
            purchase_day=5,
            purchase_month=1,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 2, 15))
    jan = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == card.id,
            BillCycle.cycle_end_month == 0,
            BillCycle.cycle_end_year == 2026,
        )
    ).first()
    feb = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == card.id,
            BillCycle.cycle_end_month == 1,
            BillCycle.cycle_end_year == 2026,
        )
    ).first()
    assert round(jan.total_amount, 2) == 100
    assert round(feb.total_amount, 2) == 200, (
        "Feb total must NOT include Jan's carryover - own spending only."
    )

    pay_bill(session, jan.id, today=date(2026, 2, 15))
    feb_after = session.get(BillCycle, feb.id)
    assert round(feb_after.total_amount, 2) == 200, (
        "Paying Jan must not retroactively change Feb's frozen total."
    )

    open_bill = session.exec(
        select(BillCycle).where(BillCycle.card_id == card.id, BillCycle.status == "open")
    ).first()
    open_carry = list(
        session.exec(
            select(BillCycleLine).where(
                BillCycleLine.bill_cycle_id == open_bill.id,
                BillCycleLine.kind == "carryover",
            )
        )
    )
    assert len(open_carry) == 1
    assert round(open_carry[0].amount, 2) == 200, "Only Feb is still unpaid."


def test_card_cycle_view_projects_future_installments(session: Session):
    """A future cycle without a persisted bill must project pending installments."""
    from app.category_utils import category_map_by_id
    from app.services.bills import card_cycle_view

    cid = _category_id(session)
    card = Card(name="Visa", closing_day=10, due_day=20)
    session.add(card)
    session.commit()
    expense = Expense(
        type="credit",
        card_id=card.id,
        description="Notebook",
        amount_total=900,
        installments=3,
        purchase_day=5,
        purchase_month=3,
        purchase_year=2026,
        category_id=cid,
    )
    session.add(expense)
    session.commit()
    materialize_closed_cycles(session, date(2026, 4, 15))

    view = card_cycle_view(
        session,
        card,
        end_month=5,
        end_year=2026,
        expenses=[expense],
        subscriptions=[],
        pix_items=[],
        category_names=category_map_by_id(session),
    )
    assert view["bill"] is None
    assert view["is_projected"] is True
    assert round(view["own_total"], 2) == 300, (
        "Future May cycle should project the 2nd of 3 installments."
    )


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


def test_two_cards_same_day_different_active_open_cycle_end_month(session: Session):
    """No mesmo dia do calendário, fechamentos 5 vs 25 colocam a fatura 'aberta' em meses distintos."""
    from app.services.finance import active_cycle_today

    cid = _category_id(session)
    narrow = Card(name="Fecha5", closing_day=5, due_day=12)
    wide = Card(name="Fecha25", closing_day=25, due_day=18)
    session.add(narrow)
    session.add(wide)
    session.commit()
    session.add(
        Expense(
            type="debit",
            card_id=narrow.id,
            description="Mercado-N",
            amount_total=50,
            installments=1,
            purchase_day=10,
            purchase_month=2,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.add(
        Expense(
            type="debit",
            card_id=wide.id,
            description="Mercado-W",
            amount_total=60,
            installments=1,
            purchase_day=10,
            purchase_month=2,
            purchase_year=2026,
            category_id=cid,
        )
    )
    session.commit()
    mid_april = date(2026, 4, 15)
    materialize_closed_cycles(session, mid_april)
    assert active_cycle_today(narrow.closing_day, mid_april) == (4, 2026)
    assert active_cycle_today(wide.closing_day, mid_april) == (3, 2026)
    open_n = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == narrow.id,
            BillCycle.status == "open",
        )
    ).first()
    open_w = session.exec(
        select(BillCycle).where(
            BillCycle.card_id == wide.id,
            BillCycle.status == "open",
        )
    ).first()
    assert open_n is not None and open_w is not None
    assert (open_n.cycle_end_month, open_n.cycle_end_year) == (4, 2026)
    assert (open_w.cycle_end_month, open_w.cycle_end_year) == (3, 2026)
    totals_n = sorted(
        (b.cycle_end_month, b.cycle_end_year, b.status)
        for b in session.exec(select(BillCycle).where(BillCycle.card_id == narrow.id))
    )
    totals_w = sorted(
        (b.cycle_end_month, b.cycle_end_year, b.status)
        for b in session.exec(select(BillCycle).where(BillCycle.card_id == wide.id))
    )
    assert len(totals_n) >= 2
    assert len(totals_w) >= 2


def test_pix_cycle_with_closing_includes_pix_item_and_pix_subscription(session: Session):
    """Fatura PIX materializada agrega avulso + assinatura PIX no mesmo ciclo."""
    from app.services.bills import lines_for_bill

    cid = _category_id(session)
    settings = session.exec(select(AppSettings)).first()
    settings.pix_closing_day = 10
    session.add(settings)
    session.commit()
    session.add(
        PixItem(
            description="Presente",
            amount=250,
            start_month=4,
            start_year=2026,
            is_recurring=False,
            category_id=cid,
        )
    )
    session.add(
        Subscription(
            description="Cloud PIX",
            amount_monthly=40,
            billing_day=8,
            start_month=0,
            start_year=2026,
            is_indefinite=True,
            payment_method="pix",
            card_id=None,
            category_id=cid,
        )
    )
    session.commit()
    materialize_closed_cycles(session, date(2026, 5, 7))
    open_pix = session.exec(
        select(BillCycle).where(BillCycle.scope == "pix", BillCycle.status == "open")
    ).first()
    assert open_pix is not None
    lines = lines_for_bill(session, open_pix)
    kinds = {line["kind"] for line in lines}
    assert "pix" in kinds
    assert "subscription" in kinds
    pix_amt = sum(line["amount"] for line in lines if line["kind"] == "pix")
    sub_amt = sum(line["amount"] for line in lines if line["kind"] == "subscription")
    assert round(pix_amt, 2) == 250.0
    assert round(sub_amt, 2) == 40.0
