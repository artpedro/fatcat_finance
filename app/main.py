from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, time, timedelta
import logging
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.db import engine, init_db
from app.routes import cards, categories, dashboard, expenses, financias, income, settings, subscriptions
from app.services.cdi import sync_cdi_daily
from app.services.savings_yield import generate_cdi_yields

logger = logging.getLogger(__name__)
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
SYNC_TIMES = (time(10, 0), time(19, 0))


def _run_cdi_and_yield_sync() -> dict:
    with Session(engine) as session:
        cdi_info = sync_cdi_daily(session)
        yield_info = generate_cdi_yields(session)
    summary = {
        "cdi_fetched": cdi_info.get("fetched", 0),
        "cdi_filled_gaps": cdi_info.get("filled_gaps", 0),
        "cdi_window_start": cdi_info.get("window_start"),
        "cdi_window_end": cdi_info.get("window_end"),
        "cdi_gap_ranges_checked": cdi_info.get("gap_ranges_checked", 0),
        "yield_created": yield_info.get("created_rows", 0),
        "yield_groups": yield_info.get("processed_groups", 0),
        "yield_window_start": yield_info.get("window_start"),
        "yield_window_end": yield_info.get("window_end"),
    }
    logger.info(
        "Financial sync summary: cdi_fetched=%s cdi_filled_gaps=%s cdi_window=%s..%s cdi_gap_ranges=%s yield_created=%s yield_groups=%s yield_window=%s..%s",
        summary["cdi_fetched"],
        summary["cdi_filled_gaps"],
        summary["cdi_window_start"],
        summary["cdi_window_end"],
        summary["cdi_gap_ranges_checked"],
        summary["yield_created"],
        summary["yield_groups"],
        summary["yield_window_start"],
        summary["yield_window_end"],
    )
    return summary


def _next_sync_instant(now_local: datetime) -> datetime:
    today = now_local.date()
    candidates = [datetime.combine(today, instant, tzinfo=SAO_PAULO_TZ) for instant in SYNC_TIMES]
    for candidate in candidates:
        if candidate > now_local:
            return candidate
    return datetime.combine(today + timedelta(days=1), SYNC_TIMES[0], tzinfo=SAO_PAULO_TZ)


async def _scheduled_sync_loop() -> None:
    while True:
        now_local = datetime.now(SAO_PAULO_TZ)
        next_instant = _next_sync_instant(now_local)
        sleep_seconds = max(1.0, (next_instant - now_local).total_seconds())
        await asyncio.sleep(sleep_seconds)
        try:
            await asyncio.to_thread(_run_cdi_and_yield_sync)
        except Exception:
            logger.exception("Scheduled financial sync failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    scheduler_task = asyncio.create_task(_scheduled_sync_loop())
    try:
        _run_cdi_and_yield_sync()
    except Exception:
        logger.exception("Financial sync failed during startup")
    try:
        yield
    finally:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


app = FastAPI(title="FatCat", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


app.include_router(settings.router)
app.include_router(categories.router)
app.include_router(dashboard.router)
app.include_router(cards.router)
app.include_router(expenses.router)
app.include_router(subscriptions.router)
app.include_router(income.router)
app.include_router(financias.router)


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse("app/static/favicon.ico")


@app.get("/pix", include_in_schema=False)
def legacy_pix_to_expenses(request: Request) -> RedirectResponse:
    """Aba PIX removida: redireciona para Lançamentos com PIX avulsos + assinaturas PIX."""
    params = dict(request.query_params)
    params["f_pay"] = "pix_all"
    return RedirectResponse(url=f"/expenses?{urlencode(params)}", status_code=303)

