from __future__ import annotations

from urllib.parse import urlencode

from fastapi import Request
from sqlmodel import Session, select

from app.models import AppSettings
from app.services.finance import MONTHS, MONTHS_FULL


def get_settings(session: Session) -> AppSettings:
    settings = session.exec(select(AppSettings)).first()
    if settings is None:
        settings = AppSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def current_period(request: Request, settings: AppSettings) -> tuple[int, int]:
    q_month = request.query_params.get("month")
    q_year = request.query_params.get("year")
    if q_month is not None and q_year is not None:
        return int(q_month), int(q_year)
    return settings.selected_month, settings.selected_year


def base_context(request: Request, month: int, year: int, settings: AppSettings) -> dict:
    query = urlencode({"month": month, "year": year})
    return {
        "request": request,
        "month": month,
        "year": year,
        "month_label": f"{MONTHS[month]} {year}",
        "month_full_label": f"{MONTHS_FULL[month]} de {year}",
        "theme": settings.theme,
        "query": query,
    }

