from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.routes.common import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/month")
def shift_month(
    delta: int = Query(0),
    path: str = Query("/dashboard"),
    session: Session = Depends(get_session),
):
    settings = get_settings(session)
    month = settings.selected_month + delta
    year = settings.selected_year
    if month > 11:
        month = 0
        year += 1
    if month < 0:
        month = 11
        year -= 1
    settings.selected_month = month
    settings.selected_year = year
    session.add(settings)
    session.commit()
    return RedirectResponse(url=f"{path}?month={month}&year={year}", status_code=303)


@router.post("/theme")
def toggle_theme(
    path: str = Query("/dashboard"),
    month: int = Query(...),
    year: int = Query(...),
    session: Session = Depends(get_session),
):
    settings = get_settings(session)
    settings.theme = "light" if settings.theme == "dark" else "dark"
    session.add(settings)
    session.commit()
    return RedirectResponse(url=f"{path}?month={month}&year={year}", status_code=303)

