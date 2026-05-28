from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session, col, select

from app.models import CdiDaily, SavingsEntry, SavingsGroup


def _signed_entry_amount(entry: SavingsEntry) -> float:
    if entry.direction == "withdrawal":
        return -float(entry.amount)
    return float(entry.amount)


def _last_cdi_date(session: Session) -> date | None:
    row = session.exec(select(CdiDaily).order_by(col(CdiDaily.ref_date).desc())).first()
    if row is None:
        return None
    try:
        return date.fromisoformat(row.ref_date)
    except ValueError:
        return None


def _cdi_map(session: Session, start: date, end: date) -> dict[date, float]:
    if start > end:
        return {}
    rows = list(
        session.exec(
            select(CdiDaily).where(CdiDaily.ref_date >= start.isoformat(), CdiDaily.ref_date <= end.isoformat())
        )
    )
    data: dict[date, float] = {}
    for row in rows:
        try:
            ref_day = date.fromisoformat(row.ref_date)
        except ValueError:
            continue
        data[ref_day] = float(row.value_pct)
    return data


def _group_entries(session: Session, group_id: str) -> list[SavingsEntry]:
    return list(
        session.exec(
            select(SavingsEntry)
            .where(SavingsEntry.group_id == group_id)
            .order_by(col(SavingsEntry.entry_date), col(SavingsEntry.created_at))
        )
    )


def _last_generated_yield_date(entries: list[SavingsEntry]) -> date | None:
    dates: list[date] = []
    for entry in entries:
        if entry.direction != "yield" or entry.source_type != "cdi":
            continue
        try:
            dates.append(date.fromisoformat(entry.entry_date))
        except ValueError:
            continue
    return max(dates) if dates else None


def _balance_before(entries: list[SavingsEntry], threshold: date) -> float:
    total = 0.0
    for entry in entries:
        try:
            entry_day = date.fromisoformat(entry.entry_date)
        except ValueError:
            continue
        if entry_day >= threshold:
            continue
        total += _signed_entry_amount(entry)
    return total


def generate_cdi_yields(session: Session, *, today: date | None = None) -> dict:
    """Create forward-only CDI yield entries for each savings group.

    Rules:
    - If a group already has CDI yields, continue from day after the last one.
    - If not, start at `today` (feature go-live forward-only).
    - Base for day D = previous-day closing balance.
    """
    if today is None:
        today = date.today()
    latest_cdi = _last_cdi_date(session)
    if latest_cdi is None:
        return {"processed_groups": 0, "created_rows": 0, "window_start": None, "window_end": None}
    upper_bound = min(today, latest_cdi)
    groups = list(session.exec(select(SavingsGroup)))
    if not groups:
        return {"processed_groups": 0, "created_rows": 0, "window_start": None, "window_end": upper_bound.isoformat()}

    created_rows = 0
    overall_window_start: date | None = None
    now = datetime.now(UTC)
    for group in groups:
        entries = _group_entries(session, group.id)
        last_yield_day = _last_generated_yield_date(entries)
        if last_yield_day is not None:
            start_day = last_yield_day + timedelta(days=1)
        else:
            start_day = today
        if start_day > upper_bound:
            continue
        if overall_window_start is None or start_day < overall_window_start:
            overall_window_start = start_day

        cdi_by_day = _cdi_map(session, start_day, upper_bound)
        if not cdi_by_day:
            continue

        existing_cdi_yield_days: set[date] = set()
        day_non_cdi_delta: dict[date, float] = defaultdict(float)
        for entry in entries:
            try:
                entry_day = date.fromisoformat(entry.entry_date)
            except ValueError:
                continue
            if entry.direction == "yield" and entry.source_type == "cdi":
                existing_cdi_yield_days.add(entry_day)
                continue
            day_non_cdi_delta[entry_day] += _signed_entry_amount(entry)

        running_balance = _balance_before(entries, start_day)
        cursor = start_day
        while cursor <= upper_bound:
            base_balance = running_balance
            created_today = 0.0
            cdi_value = cdi_by_day.get(cursor)
            if cdi_value is not None and cursor not in existing_cdi_yield_days and base_balance > 0:
                yield_amount = base_balance * (cdi_value / 100.0) * (float(group.cdi_pct or 0.0) / 100.0)
                yield_amount = round(yield_amount, 8)
                if yield_amount > 0:
                    session.add(
                        SavingsEntry(
                            group_id=group.id,
                            entry_date=cursor.isoformat(),
                            amount=yield_amount,
                            direction="yield",
                            source_type="cdi",
                            source_ref_id=cursor.isoformat(),
                            notes=f"Rendimento CDI {group.cdi_pct:.2f}%",
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    created_rows += 1
                    created_today = yield_amount
            running_balance += day_non_cdi_delta.get(cursor, 0.0) + created_today
            cursor += timedelta(days=1)
    if created_rows:
        session.commit()
    return {
        "processed_groups": len(groups),
        "created_rows": created_rows,
        "window_start": overall_window_start.isoformat() if overall_window_start else None,
        "window_end": upper_bound.isoformat(),
    }
