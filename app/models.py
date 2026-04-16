from __future__ import annotations

from datetime import UTC, datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class AppSettings(SQLModel, table=True):
    id: int | None = Field(default=1, primary_key=True)
    theme: str = Field(default="dark")
    selected_month: int = Field(default_factory=lambda: datetime.now(UTC).month - 1)
    selected_year: int = Field(default_factory=lambda: datetime.now(UTC).year)


class IncomeSource(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_income_amount"),
        CheckConstraint("start_month >= 0 AND start_month <= 11", name="ck_income_start_month"),
        CheckConstraint("end_month IS NULL OR (end_month >= 0 AND end_month <= 11)", name="ck_income_end_month"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    amount: float
    kind: str = Field(default="salary")
    is_recurring: bool = Field(default=True)
    start_month: int
    start_year: int
    end_month: int | None = None
    end_year: int | None = None
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Category(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("name", name="uq_category_name"),)

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str = Field(index=True)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Card(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("closing_day >= 0 AND closing_day <= 31", name="ck_card_closing"),
        CheckConstraint("due_day >= 0 AND due_day <= 31", name="ck_card_due"),
        CheckConstraint("limit_amount >= 0", name="ck_card_limit"),
        CheckConstraint("maintenance_amount >= 0", name="ck_card_maintenance"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    closing_day: int = Field(default=0)
    due_day: int = Field(default=5)
    color: str = Field(default="#DB8A74")
    limit_amount: float = Field(default=0)
    is_used_by_subscriptions: bool = Field(default=False)
    maintenance_type: str = Field(default="none")
    maintenance_amount: float = Field(default=0)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Expense(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("amount_total > 0", name="ck_exp_amount"),
        CheckConstraint("installments >= 1", name="ck_exp_inst"),
        CheckConstraint("purchase_day >= 1 AND purchase_day <= 31", name="ck_exp_day"),
        CheckConstraint("purchase_month >= 0 AND purchase_month <= 11", name="ck_exp_month"),
        CheckConstraint("type IN ('credit', 'debit')", name="ck_exp_type"),
        CheckConstraint("card_id <> ''", name="ck_exp_card_required"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    type: str = Field(default="credit")
    card_id: str = Field(foreign_key="card.id")
    description: str
    amount_total: float
    installments: int = Field(default=1)
    purchase_day: int
    purchase_month: int
    purchase_year: int
    category_id: str = Field(foreign_key="category.id")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Subscription(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("amount_monthly > 0", name="ck_sub_amount"),
        CheckConstraint("billing_day >= 1 AND billing_day <= 31", name="ck_sub_day"),
        CheckConstraint("start_month >= 0 AND start_month <= 11", name="ck_sub_start_month"),
        CheckConstraint("end_month IS NULL OR (end_month >= 0 AND end_month <= 11)", name="ck_sub_end_month"),
        CheckConstraint("duration_months IS NULL OR duration_months >= 1", name="ck_sub_duration"),
        CheckConstraint("payment_method IN ('card', 'pix')", name="ck_sub_method"),
        CheckConstraint(
            "(payment_method='card' AND card_id IS NOT NULL AND card_id <> '') OR payment_method='pix'",
            name="ck_sub_card_link",
        ),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    description: str
    amount_monthly: float
    billing_day: int
    start_month: int
    start_year: int
    end_month: int | None = None
    end_year: int | None = None
    duration_months: int | None = None
    is_indefinite: bool = Field(default=True)
    payment_method: str = Field(default="card")
    card_id: str | None = Field(default=None, foreign_key="card.id")
    category_id: str = Field(foreign_key="category.id")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class PixItem(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_pix_amount"),
        CheckConstraint("start_month >= 0 AND start_month <= 11", name="ck_pix_start_month"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    description: str
    amount: float
    category_id: str = Field(foreign_key="category.id")
    is_recurring: bool = Field(default=False)
    start_month: int
    start_year: int
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# Future-ready savings entities (not yet exposed in UI).
class SavingsGroup(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    name: str
    color: str = Field(default="#82C4A8")
    target_amount: float = Field(default=0)
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class SavingsEntry(SQLModel, table=True):
    __table_args__ = (
        CheckConstraint("direction IN ('deposit', 'withdrawal')", name="ck_save_direction"),
    )

    id: str = Field(default_factory=_uuid, primary_key=True)
    group_id: str = Field(foreign_key="savingsgroup.id")
    entry_date: str = Field(sa_column=Column(String, nullable=False))
    amount: float
    direction: str = Field(default="deposit")
    source_type: str = ""
    source_ref_id: str = ""
    notes: str = ""
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

