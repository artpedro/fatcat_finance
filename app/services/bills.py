"""Billing cycle persistence.

When a card (or the optional PIX pseudo-flow) crosses its closing day, the
cycle it just finished becomes a frozen `BillCycle` with a snapshot of every
line it contained. Open cycles are not snapshotted (they are computed live via
`finance.lines_for_open_cycle`) except for `carryover` lines, which represent
the total of the preceding `closed_unpaid` bill rolled forward as a single
"Fatura vencida" entry.

The module is idempotent: calling `materialize_closed_cycles` multiple times
on the same state only creates missing rows and refreshes the open cycle's
carryover to reflect the latest pay/unpay actions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Iterable

from sqlmodel import Session, select

from app.category_utils import category_map_by_id
from app.models import (
    AppSettings,
    BillCycle,
    BillCycleLine,
    Card,
    Expense,
    PixItem,
    Subscription,
)
from app.services.finance import (
    active_cycle_today,
    cycle_bounds,
    cycle_end_for_purchase,
    lines_for_open_cycle,
    lines_for_open_pix_cycle,
    mkey,
)


def _prev_cycle(em: int, ey: int) -> tuple[int, int]:
    if em == 0:
        return 11, ey - 1
    return em - 1, ey


def _next_cycle(em: int, ey: int) -> tuple[int, int]:
    if em == 11:
        return 0, ey + 1
    return em + 1, ey


def _get_bill(
    session: Session, scope: str, card_id: str | None, em: int, ey: int
) -> BillCycle | None:
    stmt = select(BillCycle).where(
        BillCycle.scope == scope,
        BillCycle.cycle_end_month == em,
        BillCycle.cycle_end_year == ey,
    )
    if card_id is None:
        stmt = stmt.where(BillCycle.card_id.is_(None))  # type: ignore[union-attr]
    else:
        stmt = stmt.where(BillCycle.card_id == card_id)
    return session.exec(stmt).first()


def _list_bills(session: Session, card_id: str | None, *, scope: str) -> list[BillCycle]:
    stmt = select(BillCycle).where(BillCycle.scope == scope)
    if card_id is None:
        stmt = stmt.where(BillCycle.card_id.is_(None))  # type: ignore[union-attr]
    else:
        stmt = stmt.where(BillCycle.card_id == card_id)
    return list(session.exec(stmt))


def _earliest_card_cycle(
    session: Session,
    card: Card,
    expenses: list[Expense],
    subscriptions: list[Subscription],
) -> tuple[int, int] | None:
    candidates: list[int] = []
    for exp in expenses:
        em, ey = cycle_end_for_purchase(
            card.closing_day, exp.purchase_day, exp.purchase_month, exp.purchase_year
        )
        candidates.append(mkey(em, ey))
    for sub in subscriptions:
        em, ey = cycle_end_for_purchase(
            card.closing_day, sub.billing_day, sub.start_month, sub.start_year
        )
        candidates.append(mkey(em, ey))
    for bill in _list_bills(session, card.id, scope="card"):
        candidates.append(mkey(bill.cycle_end_month, bill.cycle_end_year))
    if not candidates:
        return None
    earliest = min(candidates)
    return earliest % 12, earliest // 12


def _earliest_pix_cycle(
    session: Session,
    pix_closing_day: int,
    pix_items: list[PixItem],
    subscriptions: list[Subscription],
) -> tuple[int, int] | None:
    candidates: list[int] = []
    for pix in pix_items:
        em, ey = cycle_end_for_purchase(pix_closing_day, 1, pix.start_month, pix.start_year)
        candidates.append(mkey(em, ey))
    for sub in subscriptions:
        em, ey = cycle_end_for_purchase(
            pix_closing_day, sub.billing_day, sub.start_month, sub.start_year
        )
        candidates.append(mkey(em, ey))
    for bill in _list_bills(session, None, scope="pix"):
        candidates.append(mkey(bill.cycle_end_month, bill.cycle_end_year))
    if not candidates:
        return None
    earliest = min(candidates)
    return earliest % 12, earliest // 12


def _insert_lines(session: Session, bill: BillCycle, lines: Iterable[dict]) -> float:
    total = 0.0
    for line in lines:
        total += float(line["amount"])
        session.add(
            BillCycleLine(
                bill_cycle_id=bill.id,
                kind=line["kind"],
                source_ref_id=line.get("source_ref_id"),
                description=line.get("description", ""),
                category_name_snapshot=line.get("category_name_snapshot", ""),
                amount=float(line["amount"]),
                charge_day=int(line["charge_day"]),
                charge_month=int(line["charge_month"]),
                charge_year=int(line["charge_year"]),
                installment_num=line.get("installment_num"),
                installments_total=line.get("installments_total"),
                notes=line.get("notes", ""),
            )
        )
    return total


def _delete_lines(session: Session, bill_id: str, kinds: Iterable[str] | None = None) -> None:
    stmt = select(BillCycleLine).where(BillCycleLine.bill_cycle_id == bill_id)
    if kinds is not None:
        stmt = stmt.where(BillCycleLine.kind.in_(list(kinds)))  # type: ignore[union-attr]
    for row in session.exec(stmt):
        session.delete(row)


def _make_bill(
    *,
    scope: str,
    card_id: str | None,
    closing_day: int,
    due_day: int,
    end_month: int,
    end_year: int,
    status: str,
) -> BillCycle:
    sd, sm, sy, ed, em, ey = cycle_bounds(closing_day, end_month, end_year)
    return BillCycle(
        scope=scope,
        card_id=card_id,
        cycle_start_day=sd,
        cycle_start_month=sm,
        cycle_start_year=sy,
        cycle_end_day=ed,
        cycle_end_month=em,
        cycle_end_year=ey,
        closing_day_snapshot=closing_day,
        due_day_snapshot=due_day,
        status=status,
        total_amount=0.0,
    )


def _ensure_closed_card_cycle(
    session: Session,
    card: Card,
    em: int,
    ey: int,
    expenses: list[Expense],
    subscriptions: list[Subscription],
    category_names: dict[str, str],
) -> BillCycle:
    """Create (if missing) the closed_unpaid cycle ending at (em, ey) with its snapshot lines.

    If a bill already exists as `open` but time has moved past its end, we promote
    it to `closed_unpaid` and freeze a snapshot of its current contents. Already
    closed/paid bills are left untouched.
    """
    bill = _get_bill(session, "card", card.id, em, ey)
    if bill is not None and bill.status in ("closed_unpaid", "paid"):
        return bill
    if bill is None:
        bill = _make_bill(
            scope="card",
            card_id=card.id,
            closing_day=card.closing_day,
            due_day=card.due_day,
            end_month=em,
            end_year=ey,
            status="closed_unpaid",
        )
        session.add(bill)
        session.flush()
    else:
        _delete_lines(session, bill.id)
        session.flush()
        bill.status = "closed_unpaid"
    lines = lines_for_open_cycle(
        card=card,
        end_month=em,
        end_year=ey,
        expenses=expenses,
        subscriptions=subscriptions,
        category_names=category_names,
    )
    prev_m, prev_y = _prev_cycle(em, ey)
    prev_bill = _get_bill(session, "card", card.id, prev_m, prev_y)
    if prev_bill is not None and prev_bill.status == "closed_unpaid":
        lines.append(
            {
                "kind": "carryover",
                "source_ref_id": prev_bill.id,
                "description": f"Fatura vencida {prev_m + 1:02d}/{prev_y}",
                "category_name_snapshot": "Fatura vencida",
                "amount": float(prev_bill.total_amount),
                "charge_day": bill.cycle_start_day,
                "charge_month": bill.cycle_start_month,
                "charge_year": bill.cycle_start_year,
                "installment_num": None,
                "installments_total": None,
                "notes": "",
            }
        )
    total = _insert_lines(session, bill, lines)
    bill.total_amount = total
    bill.updated_at = datetime.now(UTC)
    session.add(bill)
    return bill


def _ensure_closed_pix_cycle(
    session: Session,
    pix_closing_day: int,
    em: int,
    ey: int,
    pix_items: list[PixItem],
    subscriptions: list[Subscription],
    category_names: dict[str, str],
) -> BillCycle:
    bill = _get_bill(session, "pix", None, em, ey)
    if bill is not None and bill.status in ("closed_unpaid", "paid"):
        return bill
    if bill is None:
        bill = _make_bill(
            scope="pix",
            card_id=None,
            closing_day=pix_closing_day,
            due_day=pix_closing_day,
            end_month=em,
            end_year=ey,
            status="closed_unpaid",
        )
        session.add(bill)
        session.flush()
    else:
        _delete_lines(session, bill.id)
        session.flush()
        bill.status = "closed_unpaid"
    lines = lines_for_open_pix_cycle(
        end_month=em,
        end_year=ey,
        pix_closing_day=pix_closing_day,
        pix_items=pix_items,
        subscriptions=subscriptions,
        category_names=category_names,
    )
    prev_m, prev_y = _prev_cycle(em, ey)
    prev_bill = _get_bill(session, "pix", None, prev_m, prev_y)
    if prev_bill is not None and prev_bill.status == "closed_unpaid":
        lines.append(
            {
                "kind": "carryover",
                "source_ref_id": prev_bill.id,
                "description": f"Fatura vencida {prev_m + 1:02d}/{prev_y}",
                "category_name_snapshot": "Fatura vencida",
                "amount": float(prev_bill.total_amount),
                "charge_day": bill.cycle_start_day,
                "charge_month": bill.cycle_start_month,
                "charge_year": bill.cycle_start_year,
                "installment_num": None,
                "installments_total": None,
                "notes": "",
            }
        )
    total = _insert_lines(session, bill, lines)
    bill.total_amount = total
    bill.updated_at = datetime.now(UTC)
    session.add(bill)
    return bill


def _ensure_open_cycle(
    session: Session,
    *,
    scope: str,
    card_id: str | None,
    closing_day: int,
    due_day: int,
    em: int,
    ey: int,
) -> BillCycle:
    bill = _get_bill(session, scope, card_id, em, ey)
    if bill is not None:
        return bill
    bill = _make_bill(
        scope=scope,
        card_id=card_id,
        closing_day=closing_day,
        due_day=due_day,
        end_month=em,
        end_year=ey,
        status="open",
    )
    session.add(bill)
    session.flush()
    return bill


def _refresh_open_carryover(
    session: Session, bill: BillCycle, prev_bill: BillCycle | None
) -> None:
    """Drop carryover lines on an open cycle and rebuild from the previous cycle's status."""
    _delete_lines(session, bill.id, kinds=["carryover"])
    session.flush()
    if prev_bill is not None and prev_bill.status == "closed_unpaid":
        session.add(
            BillCycleLine(
                bill_cycle_id=bill.id,
                kind="carryover",
                source_ref_id=prev_bill.id,
                description=f"Fatura vencida {prev_bill.cycle_end_month + 1:02d}/{prev_bill.cycle_end_year}",
                category_name_snapshot="Fatura vencida",
                amount=float(prev_bill.total_amount),
                charge_day=bill.cycle_start_day,
                charge_month=bill.cycle_start_month,
                charge_year=bill.cycle_start_year,
            )
        )


def materialize_closed_cycles(session: Session, today: date | None = None) -> None:
    """Self-heal BillCycle rows so every card and the PIX flow have up-to-date cycles.

    - Creates `closed_unpaid` snapshots for any past cycle that crossed its end.
    - Ensures the active cycle exists with `status='open'`.
    - Refreshes `carryover` lines on open cycles to reflect the current status
      of the preceding cycle (so pay/unpay is immediately visible).
    """
    if today is None:
        today = date.today()

    cards = list(session.exec(select(Card)))
    expenses = list(session.exec(select(Expense)))
    subscriptions = list(session.exec(select(Subscription)))
    pix_items = list(session.exec(select(PixItem)))
    category_names = category_map_by_id(session)

    settings = session.exec(select(AppSettings)).first()
    pix_closing_day = int(settings.pix_closing_day) if settings is not None else 0

    by_card_expenses: dict[str, list[Expense]] = {}
    for exp in expenses:
        by_card_expenses.setdefault(exp.card_id, []).append(exp)
    by_card_subs: dict[str, list[Subscription]] = {}
    for sub in subscriptions:
        if sub.payment_method == "card" and sub.card_id:
            by_card_subs.setdefault(sub.card_id, []).append(sub)
    pix_subs = [s for s in subscriptions if s.payment_method == "pix"]

    for card in cards:
        exp_list = by_card_expenses.get(card.id, [])
        sub_list = by_card_subs.get(card.id, [])
        active_em, active_ey = active_cycle_today(card.closing_day, today)
        earliest = _earliest_card_cycle(session, card, exp_list, sub_list)
        if earliest is None:
            earliest = (active_em, active_ey)
        em, ey = earliest
        while mkey(em, ey) < mkey(active_em, active_ey):
            _ensure_closed_card_cycle(
                session, card, em, ey, exp_list, sub_list, category_names
            )
            em, ey = _next_cycle(em, ey)
        open_bill = _ensure_open_cycle(
            session,
            scope="card",
            card_id=card.id,
            closing_day=card.closing_day,
            due_day=card.due_day,
            em=active_em,
            ey=active_ey,
        )
        prev_m, prev_y = _prev_cycle(active_em, active_ey)
        prev_bill = _get_bill(session, "card", card.id, prev_m, prev_y)
        _refresh_open_carryover(session, open_bill, prev_bill)

    if pix_closing_day > 0:
        active_em, active_ey = active_cycle_today(pix_closing_day, today)
        earliest = _earliest_pix_cycle(session, pix_closing_day, pix_items, pix_subs)
        if earliest is None:
            earliest = (active_em, active_ey)
        em, ey = earliest
        while mkey(em, ey) < mkey(active_em, active_ey):
            _ensure_closed_pix_cycle(
                session, pix_closing_day, em, ey, pix_items, pix_subs, category_names
            )
            em, ey = _next_cycle(em, ey)
        open_bill = _ensure_open_cycle(
            session,
            scope="pix",
            card_id=None,
            closing_day=pix_closing_day,
            due_day=pix_closing_day,
            em=active_em,
            ey=active_ey,
        )
        prev_m, prev_y = _prev_cycle(active_em, active_ey)
        prev_bill = _get_bill(session, "pix", None, prev_m, prev_y)
        _refresh_open_carryover(session, open_bill, prev_bill)

    session.commit()


def open_bill_live_total(
    session: Session,
    bill: BillCycle,
    *,
    card: Card | None,
    expenses: list[Expense],
    subscriptions: list[Subscription],
    pix_items: list[PixItem],
    category_names: dict[str, str],
) -> tuple[float, list[dict]]:
    """Compute the live contents + total of an open bill cycle.

    Carryover lines are persisted in DB for open cycles; all other kinds are
    recomputed from live sources. The returned list merges both so templates
    render a consistent bill.
    """
    carry = list(
        session.exec(
            select(BillCycleLine).where(
                BillCycleLine.bill_cycle_id == bill.id,
                BillCycleLine.kind == "carryover",
            )
        )
    )
    live: list[dict] = []
    if bill.scope == "card" and card is not None:
        live = lines_for_open_cycle(
            card=card,
            end_month=bill.cycle_end_month,
            end_year=bill.cycle_end_year,
            expenses=expenses,
            subscriptions=subscriptions,
            category_names=category_names,
        )
    elif bill.scope == "pix":
        live = lines_for_open_pix_cycle(
            end_month=bill.cycle_end_month,
            end_year=bill.cycle_end_year,
            pix_closing_day=bill.closing_day_snapshot,
            pix_items=pix_items,
            subscriptions=subscriptions,
            category_names=category_names,
        )
    carry_dicts = [
        {
            "kind": row.kind,
            "source_ref_id": row.source_ref_id,
            "description": row.description,
            "category_name_snapshot": row.category_name_snapshot,
            "amount": float(row.amount),
            "charge_day": row.charge_day,
            "charge_month": row.charge_month,
            "charge_year": row.charge_year,
            "installment_num": row.installment_num,
            "installments_total": row.installments_total,
            "notes": row.notes,
        }
        for row in carry
    ]
    all_lines = carry_dicts + live
    total = sum(line["amount"] for line in all_lines)
    return total, all_lines


def lines_for_bill(
    session: Session,
    bill: BillCycle,
    *,
    card: Card | None = None,
    expenses: list[Expense] | None = None,
    subscriptions: list[Subscription] | None = None,
    pix_items: list[PixItem] | None = None,
    category_names: dict[str, str] | None = None,
) -> list[dict]:
    """Return the line dicts representing a bill's content.

    Closed/paid cycles are read from `BillCycleLine`. Open cycles mix live
    computation (from current Expense/Subscription/PixItem rows) with
    persisted carryover lines.
    """
    if bill.status != "open":
        rows = list(
            session.exec(
                select(BillCycleLine).where(BillCycleLine.bill_cycle_id == bill.id)
            )
        )
        return [
            {
                "kind": row.kind,
                "source_ref_id": row.source_ref_id,
                "description": row.description,
                "category_name_snapshot": row.category_name_snapshot,
                "amount": float(row.amount),
                "charge_day": row.charge_day,
                "charge_month": row.charge_month,
                "charge_year": row.charge_year,
                "installment_num": row.installment_num,
                "installments_total": row.installments_total,
                "notes": row.notes,
            }
            for row in rows
        ]
    if category_names is None:
        category_names = category_map_by_id(session)
    if expenses is None:
        expenses = list(session.exec(select(Expense)))
    if subscriptions is None:
        subscriptions = list(session.exec(select(Subscription)))
    if pix_items is None:
        pix_items = list(session.exec(select(PixItem)))
    if card is None and bill.scope == "card" and bill.card_id:
        card = session.get(Card, bill.card_id)
    _total, lines = open_bill_live_total(
        session,
        bill,
        card=card,
        expenses=expenses,
        subscriptions=subscriptions,
        pix_items=pix_items,
        category_names=category_names,
    )
    return lines


def pay_bill(session: Session, bill_id: str) -> BillCycle | None:
    """Mark a bill as paid.

    If the bill was `open` at pay time, we snapshot its live contents into
    `BillCycleLine` rows so the historical view stays frozen even if the
    underlying Expense/Subscription rows are later edited.
    """
    bill = session.get(BillCycle, bill_id)
    if bill is None:
        return None
    if bill.status == "open":
        card = session.get(Card, bill.card_id) if bill.card_id else None
        expenses = list(session.exec(select(Expense)))
        subscriptions = list(session.exec(select(Subscription)))
        pix_items = list(session.exec(select(PixItem)))
        category_names = category_map_by_id(session)
        _total, lines = open_bill_live_total(
            session,
            bill,
            card=card,
            expenses=expenses,
            subscriptions=subscriptions,
            pix_items=pix_items,
            category_names=category_names,
        )
        _delete_lines(session, bill.id)
        session.flush()
        total = _insert_lines(session, bill, lines)
        bill.total_amount = total
    bill.status = "paid"
    bill.paid_at = datetime.now(UTC)
    bill.updated_at = datetime.now(UTC)
    session.add(bill)
    session.commit()
    materialize_closed_cycles(session)
    return bill


def unpay_bill(session: Session, bill_id: str) -> BillCycle | None:
    """Revert a bill back to closed_unpaid. Frozen lines stay as-is."""
    bill = session.get(BillCycle, bill_id)
    if bill is None:
        return None
    bill.status = "closed_unpaid"
    bill.paid_at = None
    bill.updated_at = datetime.now(UTC)
    session.add(bill)
    session.commit()
    materialize_closed_cycles(session)
    return bill
