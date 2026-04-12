from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db
from app.routes import cards, dashboard, expenses, income, pix, settings, subscriptions

app = FastAPI(title="FatCat", version="1.0.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


app.include_router(settings.router)
app.include_router(dashboard.router)
app.include_router(cards.router)
app.include_router(expenses.router)
app.include_router(subscriptions.router)
app.include_router(pix.router)
app.include_router(income.router)

