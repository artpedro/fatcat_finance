from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import and_
from sqlmodel import Session, col, select

from app.models import CdiDaily

BCB_CDI_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
CDI_START_DATE = date(2026, 1, 1)


def _to_bcb_date(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def _from_bcb_date(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()


def _fetch_cdi_range(start: date, end: date) -> list[tuple[date, float]]:
    if start > end:
        return []
    params = {
        "formato": "json",
        "dataInicial": _to_bcb_date(start),
        "dataFinal": _to_bcb_date(end),
    }
    with httpx.Client(timeout=20.0) as client:
        response = client.get(BCB_CDI_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    rows: list[tuple[date, float]] = []
    for item in payload:
        raw_date = (item.get("data") or "").strip()
        raw_value = (item.get("valor") or "").strip()
        if not raw_date or not raw_value:
            continue
        try:
            parsed_date = _from_bcb_date(raw_date)
            parsed_value = float(raw_value.replace(",", "."))
        except ValueError:
            continue
        rows.append((parsed_date, parsed_value))
    return rows


def _upsert_cdi_rows(session: Session, rows: list[tuple[date, float]]) -> int:
    now = datetime.now(UTC)
    changed = 0
    for row_date, value in rows:
        key = row_date.isoformat()
        existing = session.get(CdiDaily, key)
        if existing is None:
            session.add(CdiDaily(ref_date=key, value_pct=value))
            changed += 1
            continue
        if existing.value_pct != value:
            existing.value_pct = value
            existing.updated_at = now
            session.add(existing)
            changed += 1
    return changed


def _weekday_missing_ranges(existing_dates: set[date], start: date, end: date) -> list[tuple[date, date]]:
    missing: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in existing_dates:
            missing.append(cursor)
        cursor += timedelta(days=1)
    if not missing:
        return []
    ranges: list[tuple[date, date]] = []
    range_start = missing[0]
    range_end = missing[0]
    for current in missing[1:]:
        if current == range_end + timedelta(days=1):
            range_end = current
            continue
        ranges.append((range_start, range_end))
        range_start = current
        range_end = current
    ranges.append((range_start, range_end))
    return ranges


def sync_cdi_daily(session: Session) -> dict:
    """Sync daily CDI from BCB API and backfill weekday gaps.

    Notes:
    - Start baseline: 01/01/2026.
    - Weekends and market holidays may legitimately have no data.
    """
    today = date.today()
    if today < CDI_START_DATE:
        return {"fetched": 0, "filled_gaps": 0, "window_start": None, "window_end": None}

    latest = session.exec(select(CdiDaily).order_by(col(CdiDaily.ref_date).desc())).first()
    incremental_start = CDI_START_DATE
    if latest:
        try:
            incremental_start = date.fromisoformat(latest.ref_date) + timedelta(days=1)
        except ValueError:
            incremental_start = CDI_START_DATE

    fetched = 0
    window_start = incremental_start.isoformat()
    window_end = today.isoformat()
    if incremental_start <= today:
        fetched_rows = _fetch_cdi_range(incremental_start, today)
        fetched += _upsert_cdi_rows(session, fetched_rows)
        session.commit()

    daily_rows = session.exec(
        select(CdiDaily.ref_date).where(and_(CdiDaily.ref_date >= CDI_START_DATE.isoformat(), CdiDaily.ref_date <= today.isoformat()))
    ).all()
    existing_dates = {date.fromisoformat(value) for value in daily_rows}
    missing_ranges = _weekday_missing_ranges(existing_dates, CDI_START_DATE, today)

    filled_gaps = 0
    for start, end in missing_ranges:
        gap_rows = _fetch_cdi_range(start, end)
        if not gap_rows:
            continue
        filled_gaps += _upsert_cdi_rows(session, gap_rows)
    if filled_gaps:
        session.commit()
    return {
        "fetched": fetched,
        "filled_gaps": filled_gaps,
        "window_start": window_start,
        "window_end": window_end,
        "gap_ranges_checked": len(missing_ranges),
    }
