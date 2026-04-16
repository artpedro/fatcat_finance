from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, delete, select

from app.category_utils import category_map_by_name, seed_default_categories
from app.db import init_db, engine
from app.models import AppSettings, Card, Category, Expense, IncomeSource, PixItem, Subscription


def _reset_data(session: Session) -> None:
    session.exec(delete(Expense))
    session.exec(delete(Subscription))
    session.exec(delete(PixItem))
    session.exec(delete(IncomeSource))
    session.exec(delete(Card))
    session.exec(delete(Category))
    session.commit()


def seed_database(reset: bool = False) -> None:
    init_db()
    with Session(engine) as session:
        if reset:
            _reset_data(session)
        seed_default_categories(session)

        has_cards = session.exec(select(Card)).first() is not None
        if has_cards and not reset:
            print("Seed skipped: database already has data. Use --reset to reseed.")
            return

        settings = session.exec(select(AppSettings)).first()
        if settings:
            settings.theme = "dark"
            settings.selected_month = datetime.now(UTC).month - 1
            settings.selected_year = datetime.now(UTC).year
            session.add(settings)

        salary = IncomeSource(
            name="Salário CLT",
            amount=6200,
            kind="salary",
            is_recurring=True,
            start_month=0,
            start_year=2026,
            notes="Receita principal",
        )
        freelance = IncomeSource(
            name="Freelas",
            amount=1400,
            kind="freelance",
            is_recurring=True,
            start_month=1,
            start_year=2026,
            notes="Média mensal",
        )
        bonus = IncomeSource(
            name="Bônus anual",
            amount=2200,
            kind="bonus",
            is_recurring=False,
            start_month=5,
            start_year=2026,
            notes="Pagamento único em junho",
        )
        session.add(salary)
        session.add(freelance)
        session.add(bonus)

        visa = Card(
            name="Visa Platinum",
            closing_day=10,
            due_day=20,
            color="#DB8A74",
            limit_amount=12000,
            maintenance_type="conditional",
            maintenance_amount=24.9,
        )
        master = Card(
            name="Master Black",
            closing_day=5,
            due_day=14,
            color="#9B8FD4",
            limit_amount=18000,
            maintenance_type="none",
            maintenance_amount=0,
        )
        nubank = Card(
            name="Nubank",
            closing_day=15,
            due_day=25,
            color="#82C4A8",
            limit_amount=5000,
            maintenance_type="fixed",
            maintenance_amount=9.9,
        )
        session.add(visa)
        session.add(master)
        session.add(nubank)
        session.commit()
        session.refresh(visa)
        session.refresh(master)
        session.refresh(nubank)

        cmap = category_map_by_name(session)

        expenses = [
            Expense(
                type="credit",
                card_id=visa.id,
                description="Notebook parcelado",
                amount_total=7200,
                installments=12,
                purchase_day=12,
                purchase_month=0,
                purchase_year=2026,
                category_id=cmap["Educação"],
            ),
            Expense(
                type="credit",
                card_id=master.id,
                description="Supermercado mensal",
                amount_total=980,
                installments=1,
                purchase_day=3,
                purchase_month=3,
                purchase_year=2026,
                category_id=cmap["Mercado"],
            ),
            Expense(
                type="debit",
                card_id=nubank.id,
                description="Farmácia",
                amount_total=180,
                installments=1,
                purchase_day=9,
                purchase_month=3,
                purchase_year=2026,
                category_id=cmap["Saúde"],
            ),
            Expense(
                type="debit",
                card_id=visa.id,
                description="Combustível",
                amount_total=250,
                installments=1,
                purchase_day=10,
                purchase_month=3,
                purchase_year=2026,
                category_id=cmap["Transporte"],
            ),
        ]
        for item in expenses:
            session.add(item)

        subscriptions = [
            Subscription(
                description="Netflix",
                amount_monthly=55.9,
                billing_day=8,
                start_month=0,
                start_year=2026,
                is_indefinite=True,
                payment_method="card",
                card_id=master.id,
                category_id=cmap["Assinatura"],
            ),
            Subscription(
                description="Curso inglês",
                amount_monthly=199.9,
                billing_day=7,
                start_month=2,
                start_year=2026,
                is_indefinite=False,
                duration_months=8,
                payment_method="pix",
                category_id=cmap["Educação"],
            ),
        ]
        for item in subscriptions:
            session.add(item)

        session.commit()

        cards_count = len(session.exec(select(Card)).all())
        income_count = len(session.exec(select(IncomeSource)).all())
        expenses_count = len(session.exec(select(Expense)).all())
        subscriptions_count = len(session.exec(select(Subscription)).all())
        print("Seed complete:")
        print(f"- cards: {cards_count}")
        print(f"- income_sources: {income_count}")
        print(f"- expenses: {expenses_count}")
        print(f"- subscriptions: {subscriptions_count}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Seed SQLite database with test data.")
    parser.add_argument("--reset", action="store_true", help="Clear existing data before seeding.")
    args = parser.parse_args()
    seed_database(reset=args.reset)
