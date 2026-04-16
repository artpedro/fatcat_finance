from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.db import get_session
from app.routes.common import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/month")
def shift_month(
    request: Request,
    delta: int = Query(0),
    path: str = Query("/dashboard"),
    month: int | None = Query(None),
    year: int | None = Query(None),
    session: Session = Depends(get_session),
):
    settings = get_settings(session)
    if month is not None and year is not None:
        base_m, base_y = month, year
    else:
        base_m, base_y = settings.selected_month, settings.selected_year
    m = base_m + delta
    y = base_y
    if m > 11:
        m = 0
        y += 1
    elif m < 0:
        m = 11
        y -= 1
    settings.selected_month = m
    settings.selected_year = y
    session.add(settings)
    session.commit()
    preserved: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key in ("delta", "path"):
            continue
        preserved[key] = value
    preserved["month"] = str(m)
    preserved["year"] = str(y)
    dest = f"{path}?{urlencode(preserved)}"
    return RedirectResponse(url=dest, status_code=303)


@router.post("/theme")
def toggle_theme(request: Request, session: Session = Depends(get_session)):
    path = request.query_params.get("path", "/dashboard")
    settings = get_settings(session)
    settings.theme = "light" if settings.theme == "dark" else "dark"
    session.add(settings)
    session.commit()
    preserved: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key == "path":
            continue
        preserved[key] = value
    dest = f"{path}?{urlencode(preserved)}"
    return RedirectResponse(url=dest, status_code=303)

