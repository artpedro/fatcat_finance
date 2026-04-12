from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.db import get_session
from app.models import PixItem
from app.routes.common import base_context, current_period, get_settings
from app.services.finance import fmt_month, pix_for_month
from app.templates import brl, templates

router = APIRouter(prefix="/pix", tags=["pix"])


def _rows(session: Session, month: int, year: int) -> list[dict]:
    items = session.exec(select(PixItem)).all()
    active_ids = {item.id for item in pix_for_month(items, month, year)}
    rows = []
    for item in items:
        rows.append(
            {
                "item": item,
                "start": fmt_month(item.start_month, item.start_year),
                "active": item.id in active_ids,
                "amount_fmt": brl(item.amount),
            }
        )
    rows.sort(key=lambda row: (row["item"].start_year, row["item"].start_month), reverse=True)
    return rows


@router.get("")
def page(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"active": "pix", "pix_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "pages/pix.html", context)


@router.get("/form")
def form(request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"pix": None, "start_val": f"{year}-{month+1:02d}"})
    return templates.TemplateResponse(request, "partials/pix_form.html", context)


@router.get("/form/{pix_id}")
def form_edit(pix_id: str, request: Request, session: Session = Depends(get_session)):
    settings = get_settings(session)
    month, year = current_period(request, settings)
    pix = session.get(PixItem, pix_id)
    start_val = ""
    if pix:
        start_val = f"{pix.start_year}-{pix.start_month + 1:02d}"
    context = base_context(request, month, year, settings)
    context.update({"pix": pix, "start_val": start_val})
    return templates.TemplateResponse(request, "partials/pix_form.html", context)


@router.get("/form-clear", response_class=HTMLResponse)
def clear_form() -> str:
    return ""


@router.post("/save")
def save(
    request: Request,
    pix_id: str = Form(""),
    description: str = Form(...),
    amount: float = Form(...),
    category: str = Form("Assinatura"),
    start: str = Form(...),
    is_recurring: str = Form("false"),
    session: Session = Depends(get_session),
):
    pix = session.get(PixItem, pix_id) if pix_id else None
    if pix is None:
        pix = PixItem(description=description, amount=amount, start_month=0, start_year=2024)
    sy, sm = start.split("-")
    pix.description = description.strip()
    pix.amount = float(amount)
    pix.category = category
    pix.start_year = int(sy)
    pix.start_month = int(sm) - 1
    pix.is_recurring = is_recurring.lower() == "true"
    pix.updated_at = datetime.utcnow()
    session.add(pix)
    session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"pix_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/pix_table.html", context)


@router.delete("/{pix_id}")
def delete(pix_id: str, request: Request, session: Session = Depends(get_session)):
    pix = session.get(PixItem, pix_id)
    if pix:
        session.delete(pix)
        session.commit()
    settings = get_settings(session)
    month, year = current_period(request, settings)
    context = base_context(request, month, year, settings)
    context.update({"pix_rows": _rows(session, month, year)})
    return templates.TemplateResponse(request, "partials/pix_table.html", context)

