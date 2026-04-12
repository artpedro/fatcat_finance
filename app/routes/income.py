from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import IncomeSource
from app.routes.common import base_context, current_period, get_settings
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
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"active": "income", "income_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "pages/income.html", context)


@router.get("/form")
def form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"source": None, "start_val": f"{year}-{month+1:02d}", "end_val": ""})
    return templates.TemplateResponse(request, "partials/income_form.html", context)


@router.get("/form/{income_id}")
def form_edit(income_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    source = session.get(IncomeSource, income_id)
    start_val = ""
    end_val = ""
    if source:
        start_val = f"{source.start_year}-{source.start_month + 1:02d}"
        if source.end_month is not None and source.end_year is not None:
            end_val = f"{source.end_year}-{source.end_month + 1:02d}"
    context = base_context(request, month, year, settings)
    context.update({"source": source, "start_val": start_val, "end_val": end_val})
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
    is_recurring: str = Form("true"),
    notes: str = Form(""),
    session: Session = Depends(get_session),
):
    source = session.get(IncomeSource, income_id) if income_id else None
    if source is None:
        source = IncomeSource(name=name, amount=amount, start_month=0, start_year=2024)
    sy, sm = start.split("-")
    source.name = name.strip()
    source.amount = float(amount)
    source.kind = kind
    source.start_year = int(sy)
    source.start_month = int(sm) - 1
    source.is_recurring = is_recurring.lower() == "true"
    if end:
        ey, em = end.split("-")
        source.end_year = int(ey)
        source.end_month = int(em) - 1
    else:
        source.end_year = None
        source.end_month = None
    source.notes = notes
    source.updated_at = datetime.utcnow()
    session.add(source)
    session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
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
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"income_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/income_table.html", context)

