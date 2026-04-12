from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, select

from app.db import engine
from app.main import app
from app.models import AppSettings, Card, Expense


def reset_db() -> None:
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(AppSettings())
        session.commit()


def test_expense_requires_existing_card():
    reset_db()
    with TestClient(app) as client:
        response = client.post(
            "/expenses/save?month=0&year=2026",
            data={
                "description": "Mercado",
                "exp_type": "debit",
                "card_id": "no-card",
                "amount_total": 100,
                "installments": 1,
                "purchase_date": "2026-01-10",
                "category": "Mercado",
            },
        )
    assert response.status_code == 400


def test_debit_forces_single_installment():
    reset_db()
    with Session(engine) as session:
        card = Card(name="Test", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        card_id = card.id

    with TestClient(app) as client:
        response = client.post(
            "/expenses/save?month=0&year=2026",
            data={
                "description": "Conta",
                "exp_type": "debit",
                "card_id": card_id,
                "amount_total": 80,
                "installments": 8,
                "purchase_date": "2026-01-03",
                "category": "Casa",
            },
        )
    assert response.status_code == 200
    with Session(engine) as session:
        expense = session.exec(select(Expense)).first()
    assert expense is not None
    assert expense.installments == 1


def test_subscription_card_rules_and_pix_flow():
    reset_db()
    with Session(engine) as session:
        card = Card(name="Master", closing_day=8, due_day=18)
        session.add(card)
        session.commit()
        session.refresh(card)
        card_id = card.id

    with TestClient(app) as client:
        bad = client.post(
            "/subscriptions/save?month=0&year=2026",
            data={
                "description": "Streaming",
                "amount_monthly": 39.9,
                "billing_day": 5,
                "payment_method": "card",
                "card_id": "",
                "start": "2026-01",
                "is_indefinite": "true",
                "pix_category": "Assinatura",
            },
        )
        good = client.post(
            "/subscriptions/save?month=0&year=2026",
            data={
                "description": "Storage",
                "amount_monthly": 12.0,
                "billing_day": 2,
                "payment_method": "pix",
                "card_id": card_id,
                "start": "2026-01",
                "is_indefinite": "false",
                "duration_months": 6,
                "pix_category": "Serviço",
            },
        )

    assert bad.status_code == 400
    assert good.status_code == 200
