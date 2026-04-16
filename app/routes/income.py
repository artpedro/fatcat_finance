from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.form_dates import month_year_to_date_iso, parse_iso_date_to_month_year
from app.models import IncomeSource
from app.routes.common import base_context, get_settings, resolve_and_sync_period
from app.services.finance import fmt_month, is_income_active
from app.templates import brl, templates

router = APIRouter(prefix="/income", tags=["income"])


def _rows(session: Session, month: int, year: int) -> list[dict]:
    sources = session.exec(select(IncomeSource)).all()
    rows: list[dict] = []
    for source in sources:
        end = "Sem fim"
        if source.end_month is not None and source.end_year is not None:
            end = fmt_month(source.end_month, source.end_year)
        rows.append(
            {
                "source": source,
                "start": fmt_month(source.start_month, source.start_year),
                "end": end,
                "active": is_income_active(source, month, year),
                "amount_fmt": brl(source.amount),
            }
        )
    rows.sort(key=lambda row: (row["source"].start_year, row["source"].start_month), reverse=True)
    return rows


@router.get("")
def page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"active": "income", "income_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "pages/income.html", context)


@router.get("/form")
def form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update(
        {
            "source": None,
            "start_date_iso": month_year_to_date_iso(year, month, 1),
            "end_date_iso": "",
            "income_has_end": False,
        }
    )
    return templates.TemplateResponse(request, "partials/income_form.html", context)


@router.get("/form/{income_id}")
def form_edit(income_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    source = session.get(IncomeSource, income_id)
    start_date_iso = month_year_to_date_iso(year, month, 1)
    end_date_iso = ""
    income_has_end = False
    if source:
        start_date_iso = month_year_to_date_iso(source.start_year, source.start_month, 1)
        if source.end_month is not None and source.end_year is not None:
            end_date_iso = month_year_to_date_iso(source.end_year, source.end_month, 1)
            income_has_end = True
    context = base_context(request, month, year, settings)
    context.update(
        {
            "source": source,
            "start_date_iso": start_date_iso,
            "end_date_iso": end_date_iso,
            "income_has_end": income_has_end,
        }
    )
    return templates.TemplateResponse(request, "partials/income_form.html", context)


@router.get("/form-clear", response_class=HTMLResponse)
def clear_form() -> str:
    return ""


@router.post("/save")
def save(
    request: Request,
    income_id: str = Form(""),
    name: str = Form(...),
    amount: float = Form(...),
    kind: str = Form("salary"),
    start: str = Form(...),
    end: str = Form(""),
    has_end: str = Form(""),
    is_recurring: str = Form("true"),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    source = session.get(IncomeSource, income_id) if income_id else None
    if source is None:
        source = IncomeSource(name=name, amount=amount, start_month=0, start_year=2024)
    try:
        sm_start, sy_start = parse_iso_date_to_month_year(start)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data de início inválida.") from exc
    source.name = name.strip()
    source.amount = float(amount)
    source.kind = kind
    source.start_year = sy_start
    source.start_month = sm_start
    source.is_recurring = (is_recurring or "").strip().lower() == "true"
    wants_end = (has_end or "").strip() == "1"
    if wants_end:
        end_raw = (end or "").strip()
        if not end_raw:
            raise HTTPException(status_code=400, detail="Informe a data de término ou desmarque a opção.")
        try:
            em, ey = parse_iso_date_to_month_year(end_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Data de término inválida.") from exc
        source.end_month = em
        source.end_year = ey
    else:
        source.end_year = None
        source.end_month = None
    source.notes = notes
    source.updated_at = datetime.utcnow()
    session.add(source)
    session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"income_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/income_table.html", context)


@router.delete("/{income_id}")
def delete(income_id: str, request: Request, session: Session = Depends(get_session)):
    source = session.get(IncomeSource, income_id)
    if source:
        session.delete(source)
        session.commit()
    settings = get_settings(session)
    month, year = resolve_and_sync_period(request, session, settings)
    context = base_context(request, month, year, settings)
    context.update({"income_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/income_table.html", context)

