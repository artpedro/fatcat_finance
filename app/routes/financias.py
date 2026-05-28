from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, col, select

from app.db import get_session
from app.models import CdiDaily, SavingsEntry, SavingsGroup
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.templates import brl, templates

router = APIRouter(prefix="/financias", tags=["financias"])


def _fmt_entry_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d/%m/%Y")


def _signed_entry_amount(entry: SavingsEntry) -> float:
    if entry.direction == "withdrawal":
        return -float(entry.amount)
    return float(entry.amount)


def _group_rows(session: Session, period_start: date, period_end: date) -> tuple[list[dict], dict]:
    groups = list(session.exec(select(SavingsGroup).order_by(col(SavingsGroup.created_at).desc())))
    entries = list(
        session.exec(
            select(SavingsEntry).order_by(col(SavingsEntry.entry_date).desc(), col(SavingsEntry.created_at).desc())
        )
    )
    entries_by_group: dict[str, list[SavingsEntry]] = {}
    for entry in entries:
        entries_by_group.setdefault(entry.group_id, []).append(entry)

    rows: list[dict] = []
    period_total_rendimento = 0.0
    all_time_total_rendimento = 0.0
    today_total_rendimento = 0.0
    today = date.today()
    for group in groups:
        group_entries = entries_by_group.get(group.id, [])
        balance = 0.0
        period_rendimento = 0.0
        all_time_rendimento = 0.0
        movement_rows: list[dict] = []
        for entry in group_entries:
            signed_amount = _signed_entry_amount(entry)
            balance += signed_amount
            try:
                entry_day = date.fromisoformat(entry.entry_date)
            except ValueError:
                entry_day = None
            if entry.direction == "yield":
                all_time_rendimento += float(entry.amount)
                if entry_day and period_start <= entry_day <= period_end:
                    period_rendimento += float(entry.amount)
                if entry_day and entry_day == today:
                    today_total_rendimento += float(entry.amount)
            movement_rows.append(
                {
                    "entry": entry,
                    "direction_label": (
                        "Aporte"
                        if entry.direction == "deposit"
                        else ("Retirada" if entry.direction == "withdrawal" else "Rendimento")
                    ),
                    "signed_amount": signed_amount,
                    "signed_amount_fmt": brl(signed_amount),
                    "amount_fmt": brl(entry.amount),
                    "entry_date_fmt": _fmt_entry_date(entry.entry_date),
                }
            )

        period_total_rendimento += period_rendimento
        all_time_total_rendimento += all_time_rendimento

        rows.append(
            {
                "group": group,
                "balance": balance,
                "balance_fmt": brl(balance),
                "target_fmt": brl(group.target_amount),
                "period_rendimento": period_rendimento,
                "period_rendimento_fmt": brl(period_rendimento),
                "all_time_rendimento": all_time_rendimento,
                "all_time_rendimento_fmt": brl(all_time_rendimento),
                "movements": movement_rows,
            }
        )
    totals = {
        "period_total_rendimento": period_total_rendimento,
        "period_total_rendimento_fmt": brl(period_total_rendimento),
        "all_time_total_rendimento": all_time_total_rendimento,
        "all_time_total_rendimento_fmt": brl(all_time_total_rendimento),
        "today_total_rendimento": today_total_rendimento,
        "today_total_rendimento_fmt": brl(today_total_rendimento),
    }
    return rows, totals


def _start_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _subtract_months(day: date, months: int) -> date:
    year = day.year
    month = day.month - months
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _resolve_chart_window(
    request: Request, session: Session, today: date
) -> tuple[date, date, str, str, str]:
    entries = list(session.exec(select(SavingsEntry.entry_date).order_by(col(SavingsEntry.entry_date))))
    earliest = today
    if entries:
        try:
            earliest = date.fromisoformat(entries[0])
        except ValueError:
            earliest = today

    months_window = (request.query_params.get("months_window") or "6").strip().lower()
    from_raw = (request.query_params.get("chart_from") or "").strip()
    to_raw = (request.query_params.get("chart_to") or "").strip()
    if not to_raw:
        to_raw = today.isoformat()
    try:
        to_date = date.fromisoformat(to_raw)
    except ValueError:
        to_date = today
        to_raw = to_date.isoformat()

    if months_window == "all":
        from_date = earliest
        from_raw = from_date.isoformat()
    elif months_window in {"1", "3", "6", "12", "24"}:
        window = int(months_window)
        from_date = _subtract_months(_month_start(today), window - 1)
        from_raw = from_date.isoformat()
    else:
        months_window = "custom"
        if from_raw:
            try:
                from_date = date.fromisoformat(from_raw)
            except ValueError:
                from_date = _subtract_months(_month_start(today), 5)
                from_raw = from_date.isoformat()
        else:
            from_date = _subtract_months(_month_start(today), 5)
            from_raw = from_date.isoformat()

    if from_date > to_date:
        from_date, to_date = to_date, from_date
        from_raw = from_date.isoformat()
        to_raw = to_date.isoformat()

    return from_date, to_date, months_window, from_raw, to_raw


def _growth_chart_payload(session: Session, from_date: date, to_date: date) -> dict:
    groups = list(session.exec(select(SavingsGroup).order_by(col(SavingsGroup.created_at))))
    entries = list(
        session.exec(
            select(SavingsEntry).order_by(col(SavingsEntry.entry_date), col(SavingsEntry.created_at))
        )
    )
    if not groups:
        return {"labels": [], "rendimento_by_box_weekly": [], "rendimento_total_weekly": [], "rendimento_running_total": []}
    first_week = _start_of_week(from_date)
    last_week = _start_of_week(to_date)
    week_starts: list[date] = []
    cursor = first_week
    while cursor <= last_week:
        week_starts.append(cursor)
        cursor += timedelta(days=7)

    week_yield_by_group: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for entry in entries:
        if entry.direction != "yield":
            continue
        try:
            entry_day = date.fromisoformat(entry.entry_date)
        except ValueError:
            continue
        if entry_day < first_week or entry_day > to_date:
            continue
        week_yield_by_group[entry.group_id][_start_of_week(entry_day)] += float(entry.amount)

    labels = [week.strftime("%d/%m/%Y") for week in week_starts]
    datasets: list[dict] = []
    for group in groups:
        series: list[float] = []
        for week in week_starts:
            series.append(round(week_yield_by_group[group.id].get(week, 0.0), 8))
        datasets.append(
            {
                "label": group.name,
                "color": group.color or "#82C4A8",
                "values": series,
            }
        )
    rendimento_total_weekly: list[float] = []
    rendimento_running_total: list[float] = []
    running = 0.0
    for idx in range(len(week_starts)):
        week_total = sum(dataset["values"][idx] for dataset in datasets)
        running += week_total
        rendimento_total_weekly.append(round(week_total, 8))
        rendimento_running_total.append(round(running, 8))
    return {
        "labels": labels,
        "rendimento_by_box_weekly": datasets,
        "rendimento_total_weekly": rendimento_total_weekly,
        "rendimento_running_total": rendimento_running_total,
    }


def _cdi_chart_payload(session: Session) -> dict:
    rows = list(
        session.exec(
            select(CdiDaily).where(CdiDaily.ref_date >= "2026-01-01").order_by(col(CdiDaily.ref_date))
        )
    )
    labels = [_fmt_entry_date(row.ref_date) for row in rows]
    values = [round(float(row.value_pct), 6) for row in rows]
    return {"labels": labels, "values": values}


def _gross_balance_chart_payload(groups: list[dict]) -> dict:
    ordered = sorted(groups, key=lambda row: row["balance"], reverse=True)
    return {
        "labels": [row["group"].name for row in ordered],
        "values": [round(float(row["balance"]), 2) for row in ordered],
        "colors": [row["group"].color or "#82C4A8" for row in ordered],
    }


def _gross_evolution_chart_payload(session: Session, from_date: date, to_date: date) -> dict:
    groups = list(session.exec(select(SavingsGroup).order_by(col(SavingsGroup.created_at))))
    entries = list(
        session.exec(
            select(SavingsEntry).order_by(col(SavingsEntry.entry_date), col(SavingsEntry.created_at))
        )
    )
    if not groups:
        return {"labels": [], "datasets": [], "gross_total_weekly": []}

    first_week = _start_of_week(from_date)
    last_week = _start_of_week(to_date)
    week_starts: list[date] = []
    cursor = first_week
    while cursor <= last_week:
        week_starts.append(cursor)
        cursor += timedelta(days=7)

    base_balance_by_group: dict[str, float] = defaultdict(float)
    week_delta_by_group: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    for entry in entries:
        try:
            entry_day = date.fromisoformat(entry.entry_date)
        except ValueError:
            continue
        amount = _signed_entry_amount(entry)
        if entry_day < first_week:
            base_balance_by_group[entry.group_id] += amount
            continue
        if entry_day > to_date:
            continue
        week_delta_by_group[entry.group_id][_start_of_week(entry_day)] += amount

    labels = [week.strftime("%d/%m/%Y") for week in week_starts]
    datasets: list[dict] = []
    for group in groups:
        running = base_balance_by_group.get(group.id, 0.0)
        series: list[float] = []
        for week in week_starts:
            running += week_delta_by_group[group.id].get(week, 0.0)
            series.append(round(running, 8))
        datasets.append(
            {
                "label": group.name,
                "color": group.color or "#82C4A8",
                "values": series,
            }
        )
    gross_total_weekly: list[float] = []
    for idx in range(len(week_starts)):
        gross_total_weekly.append(round(sum(dataset["values"][idx] for dataset in datasets), 8))
    return {"labels": labels, "datasets": datasets, "gross_total_weekly": gross_total_weekly}


@router.get("")
def page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    today = date.today()
    chart_from, chart_to, months_window, chart_from_raw, chart_to_raw = _resolve_chart_window(
        request, session, today
    )
    groups, totals = _group_rows(session, chart_from, chart_to)
    total_balance = sum(row["balance"] for row in groups)
    context = base_context(request, month, year, settings)
    context.update(
        {
            "active": "financias",
            "groups": groups,
            "total_balance": total_balance,
            "total_balance_fmt": brl(total_balance),
            "today_iso": today.isoformat(),
            "growth_chart": _growth_chart_payload(session, chart_from, chart_to),
            "gross_chart": _gross_balance_chart_payload(groups),
            "gross_evolution_chart": _gross_evolution_chart_payload(session, chart_from, chart_to),
            "cdi_chart": _cdi_chart_payload(session),
            "chart_from": chart_from_raw,
            "chart_to": chart_to_raw,
            "months_window": months_window,
            "period_total_rendimento": totals["period_total_rendimento"],
            "period_total_rendimento_fmt": totals["period_total_rendimento_fmt"],
            "all_time_total_rendimento": totals["all_time_total_rendimento"],
            "all_time_total_rendimento_fmt": totals["all_time_total_rendimento_fmt"],
            "today_total_rendimento": totals["today_total_rendimento"],
            "today_total_rendimento_fmt": totals["today_total_rendimento_fmt"],
        }
    )
    return templates.TemplateResponse(request, "pages/financias.html", context)


@router.post("/groups")
def create_group(
    name: str = Form(...),
    color: str = Form("#82C4A8"),
    cdi_pct: float = Form(100.0),
    target_amount: float = Form(0),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    cleaned_name = name.strip()
    if not cleaned_name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")
    group = SavingsGroup(
        name=cleaned_name,
        color=(color or "#82C4A8").strip() or "#82C4A8",
        cdi_pct=max(0.0, float(cdi_pct or 0)),
        target_amount=max(0.0, float(target_amount or 0)),
        notes=notes.strip(),
    )
    session.add(group)
    session.commit()
    return RedirectResponse(url="/financias", status_code=303)


@router.post("/groups/{group_id}/delete")
def delete_group(group_id: str, session: Session = Depends(get_session)):
    group = session.get(SavingsGroup, group_id)
    if group:
        for entry in session.exec(select(SavingsEntry).where(SavingsEntry.group_id == group_id)):
            session.delete(entry)
        session.delete(group)
        session.commit()
    return RedirectResponse(url="/financias", status_code=303)


@router.post("/groups/{group_id}/cdi")
def update_group_cdi(
    group_id: str,
    cdi_pct: float = Form(...),
    session: Session = Depends(get_session),
):
    group = session.get(SavingsGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.")
    group.cdi_pct = max(0.0, float(cdi_pct or 0))
    group.updated_at = datetime.now(UTC)
    session.add(group)
    session.commit()
    return RedirectResponse(url="/financias", status_code=303)


@router.post("/groups/{group_id}/entries")
def add_entry(
    group_id: str,
    amount: float = Form(...),
    direction: str = Form("deposit"),
    entry_date: str = Form(""),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    group = session.get(SavingsGroup, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.")
    if float(amount) <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser maior que zero.")
    direction_value = (direction or "deposit").strip().lower()
    if direction_value not in ("deposit", "withdrawal"):
        raise HTTPException(status_code=400, detail="Direção inválida.")

    selected_date = (entry_date or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(selected_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data inválida.") from exc

    entry = SavingsEntry(
        group_id=group_id,
        entry_date=selected_date,
        amount=float(amount),
        direction=direction_value,
        source_type="manual",
        source_ref_id="",
        notes=notes.strip(),
    )
    group.updated_at = datetime.now(UTC)
    session.add(entry)
    session.add(group)
    session.commit()
    return RedirectResponse(url="/financias", status_code=303)
