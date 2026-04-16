from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes import cards, categories, dashboard, expenses, income, settings, subscriptions

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="FatCat", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


app.include_router(settings.router)
app.include_router(categories.router)
app.include_router(dashboard.router)
app.include_router(cards.router)
app.include_router(expenses.router)
app.include_router(subscriptions.router)
app.include_router(income.router)


@app.get("/pix", include_in_schema=False)
def legacy_pix_to_expenses(request: Request) -> RedirectResponse:
    """Aba PIX removida: redireciona para Lançamentos com PIX avulsos + assinaturas PIX."""
    params = dict(request.query_params)
    params["f_pay"] = "pix_all"
    return RedirectResponse(url=f"/expenses?{urlencode(params)}", status_code=303)

