import tempfile
from collections.abc import Generator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

from app.category_utils import seed_default_categories
from app.db import get_session
from app.main import app
from app.models import AppSettings, Card, Category, Expense, PixItem, Subscription


@pytest.fixture
def test_engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test-fatcat.db"
        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(AppSettings())
            session.commit()
            seed_default_categories(session)
        yield engine


@pytest.fixture
def client(test_engine):
    def _get_test_session() -> Generator[Session, None, None]:
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def reset_db(test_engine) -> None:
    SQLModel.metadata.drop_all(test_engine)
    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        session.add(AppSettings())
        session.commit()
        seed_default_categories(session)


def _category_id(session: Session, name: str) -> str:
    row = session.exec(select(Category).where(Category.name == name)).first()
    assert row is not None
    return row.id


def test_expense_requires_existing_card(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        mercado = _category_id(session, "Mercado")
    response = client.post(
        "/expenses/save?month=0&year=2026",
        data={
            "description": "Mercado",
            "exp_type": "debit",
            "card_id": "no-card",
            "amount_total": 100,
            "installments": 1,
            "purchase_date": "2026-01-10",
            "category_id": mercado,
        },
    )
    assert response.status_code == 400


def test_debit_forces_single_installment(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="Test", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        card_id = card.id
        casa = _category_id(session, "Casa")

    response = client.post(
        "/expenses/save?month=0&year=2026",
        data={
            "description": "Conta",
            "exp_type": "debit",
            "card_id": card_id,
            "amount_total": 80,
            "installments": 8,
            "purchase_date": "2026-01-03",
            "category_id": casa,
        },
    )
    assert response.status_code == 200
    with Session(test_engine) as session:
        expense = session.exec(select(Expense)).first()
    assert expense is not None
    assert expense.installments == 1


def test_subscription_card_rules_and_pix_flow(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="Master", closing_day=8, due_day=18)
        session.add(card)
        session.commit()
        session.refresh(card)
        card_id = card.id
        assinatura = _category_id(session, "Assinatura")
        servico = _category_id(session, "Serviço")

    bad = client.post(
        "/subscriptions/save?month=0&year=2026",
        data={
            "description": "Streaming",
            "amount_monthly": 39.9,
            "billing_day": 5,
            "payment_method": "card",
            "card_id": "",
            "start": "2026-01-01",
            "category_id": assinatura,
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
            "start": "2026-01-01",
            "duration_months": "6",
            "category_id": servico,
        },
    )

    assert bad.status_code == 400
    assert good.status_code == 200


def test_expenses_period_filter_hides_other_month_debit(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="Filter Card", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        casa = _category_id(session, "Casa")
        session.add(
            Expense(
                type="debit",
                card_id=card.id,
                description="Janeiro",
                amount_total=100,
                installments=1,
                purchase_day=3,
                purchase_month=0,
                purchase_year=2026,
                category_id=casa,
            )
        )
        session.add(
            Expense(
                type="debit",
                card_id=card.id,
                description="Fevereiro",
                amount_total=120,
                installments=1,
                purchase_day=3,
                purchase_month=1,
                purchase_year=2026,
                category_id=casa,
            )
        )
        session.commit()

    january_only = client.get("/expenses?month=0&year=2026&period=month")
    all_periods = client.get("/expenses?month=0&year=2026&period=all")
    assert january_only.status_code == 200
    assert all_periods.status_code == 200
    assert "Janeiro" in january_only.text
    assert "Fevereiro" not in january_only.text
    assert "Fevereiro" in all_periods.text


def test_expenses_month_filter_no_error_for_future_credit(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="Credit Card", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        outros = _category_id(session, "Outros")
        session.add(
            Expense(
                type="credit",
                card_id=card.id,
                description="Parcela Futuro",
                amount_total=600,
                installments=3,
                purchase_day=20,
                purchase_month=2,
                purchase_year=2026,
                category_id=outros,
            )
        )
        session.commit()

    response = client.get("/expenses?month=0&year=2026&period=month")
    assert response.status_code == 200
    assert "Parcela Futuro" not in response.text


def test_month_navigation_uses_current_page_month(client, test_engine):
    """Changing month must advance from the URL month, not stale AppSettings alone."""
    r = client.get(
        "/settings/month?delta=1&path=/expenses&month=5&year=2026",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/expenses?month=6&year=2026"


def test_month_navigation_preserves_lancamentos_filters(client, test_engine):
    """Sidebar month arrows must keep Meio / período / cartão in the query string."""
    r = client.get(
        "/settings/month?delta=1&path=/expenses&month=0&year=2026&f_pay=pix_sub&period=all",
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = urlparse(r.headers["location"])
    assert loc.path == "/expenses"
    qs = parse_qs(loc.query)
    assert qs["month"] == ["1"]
    assert qs["year"] == ["2026"]
    assert qs["f_pay"] == ["pix_sub"]
    assert qs["period"] == ["all"]


def test_expenses_filter_pix_shows_only_pix_subscriptions(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="C1", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        assinatura = _category_id(session, "Assinatura")
        outros = _category_id(session, "Outros")
        session.add(
            Expense(
                type="debit",
                card_id=card.id,
                description="Compra cartão",
                amount_total=50,
                installments=1,
                purchase_day=5,
                purchase_month=0,
                purchase_year=2026,
                category_id=outros,
            )
        )
        session.add(
            Subscription(
                description="Sub PIX",
                amount_monthly=10.0,
                billing_day=5,
                start_month=0,
                start_year=2026,
                is_indefinite=True,
                payment_method="pix",
                category_id=assinatura,
            )
        )
        session.commit()

    all_rows = client.get("/expenses?month=0&year=2026&period=month")
    pix_only = client.get("/expenses?month=0&year=2026&period=month&f_pay=pix")
    assert all_rows.status_code == 200
    assert pix_only.status_code == 200
    assert "Compra cartão" in all_rows.text
    assert "Sub PIX" in all_rows.text
    assert "Compra cartão" not in pix_only.text
    assert "Sub PIX" in pix_only.text


def test_expenses_filter_card_buy_excludes_subscriptions_and_pix(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="C1", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        outros = _category_id(session, "Outros")
        assinatura = _category_id(session, "Assinatura")
        session.add(
            Expense(
                type="debit",
                card_id=card.id,
                description="Só compra",
                amount_total=40,
                installments=1,
                purchase_day=5,
                purchase_month=0,
                purchase_year=2026,
                category_id=outros,
            )
        )
        session.add(
            Subscription(
                description="Sub cartão",
                amount_monthly=9.0,
                billing_day=5,
                start_month=0,
                start_year=2026,
                is_indefinite=True,
                payment_method="card",
                card_id=card.id,
                category_id=assinatura,
            )
        )
        session.add(
            PixItem(
                description="Pix avulso",
                amount=20.0,
                category_id=outros,
                is_recurring=False,
                start_month=0,
                start_year=2026,
            )
        )
        session.commit()

    r = client.get("/expenses?month=0&year=2026&period=month&f_pay=card_buy")
    assert r.status_code == 200
    assert "Só compra" in r.text
    assert "Sub cartão" not in r.text
    assert "Pix avulso" not in r.text


def test_expenses_filter_pix_buy_shows_only_pix_items(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        card = Card(name="C1", closing_day=10, due_day=20)
        session.add(card)
        session.commit()
        session.refresh(card)
        outros = _category_id(session, "Outros")
        assinatura = _category_id(session, "Assinatura")
        session.add(
            Expense(
                type="debit",
                card_id=card.id,
                description="Lançamento só cartão",
                amount_total=10,
                installments=1,
                purchase_day=5,
                purchase_month=0,
                purchase_year=2026,
                category_id=outros,
            )
        )
        session.add(
            Subscription(
                description="Assinatura PIX só",
                amount_monthly=5.0,
                billing_day=5,
                start_month=0,
                start_year=2026,
                is_indefinite=True,
                payment_method="pix",
                category_id=assinatura,
            )
        )
        session.add(
            PixItem(
                description="Transferência",
                amount=33.0,
                category_id=outros,
                is_recurring=False,
                start_month=0,
                start_year=2026,
            )
        )
        session.commit()

    r = client.get("/expenses?month=0&year=2026&period=month&f_pay=pix_buy")
    assert r.status_code == 200
    assert "Transferência" in r.text
    assert "Lançamento só cartão" not in r.text
    assert "Assinatura PIX só" not in r.text


def test_legacy_pix_path_redirects_to_expenses_pix_filter(client, test_engine):
    r = client.get("/pix?month=2&year=2026", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/expenses?month=2&year=2026&f_pay=pix_all"


def test_active_subscription_appears_on_expenses_page(client, test_engine):
    reset_db(test_engine)
    with Session(test_engine) as session:
        assinatura = _category_id(session, "Assinatura")
        session.add(
            Subscription(
                description="Streaming mensal",
                amount_monthly=39.9,
                billing_day=10,
                start_month=0,
                start_year=2026,
                is_indefinite=True,
                payment_method="pix",
                category_id=assinatura,
            )
        )
        session.commit()

    response = client.get("/expenses?month=0&year=2026&period=month")
    assert response.status_code == 200
    assert "Streaming mensal" in response.text
    assert "Assinatura" in response.text
