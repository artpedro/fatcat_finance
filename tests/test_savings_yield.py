from datetime import date, timedelta

from sqlmodel import Session, SQLModel, create_engine, select

from app.models import CdiDaily, SavingsEntry, SavingsGroup
from app.services.savings_yield import generate_cdi_yields


def _engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False})


def _insert_cdi(session: Session, day: date, value_pct: float) -> None:
    session.add(CdiDaily(ref_date=day.isoformat(), value_pct=value_pct))


def test_yield_generation_is_forward_only_when_no_prior_rendimento():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    today = date(2026, 5, 26)
    with Session(engine) as session:
        group = SavingsGroup(name="Reserva", cdi_pct=100.0)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=(today - timedelta(days=2)).isoformat(),
                amount=1000.0,
                direction="deposit",
                source_type="manual",
            )
        )
        _insert_cdi(session, today - timedelta(days=1), 0.0534)
        _insert_cdi(session, today, 0.0534)
        session.commit()

        result = generate_cdi_yields(session, today=today)
        assert result["created_rows"] == 1
        yields = session.exec(
            select(SavingsEntry).where(
                SavingsEntry.group_id == group.id,
                SavingsEntry.direction == "yield",
                SavingsEntry.source_type == "cdi",
            )
        ).all()
        assert len(yields) == 1
        assert yields[0].entry_date == today.isoformat()


def test_yield_generation_is_idempotent():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    today = date(2026, 5, 26)
    with Session(engine) as session:
        group = SavingsGroup(name="Reserva", cdi_pct=100.0)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=(today - timedelta(days=1)).isoformat(),
                amount=2000.0,
                direction="deposit",
                source_type="manual",
            )
        )
        _insert_cdi(session, today, 0.0534)
        session.commit()

        first = generate_cdi_yields(session, today=today)
        second = generate_cdi_yields(session, today=today)
        assert first["created_rows"] == 1
        assert second["created_rows"] == 0
        yields = session.exec(
            select(SavingsEntry).where(
                SavingsEntry.group_id == group.id,
                SavingsEntry.direction == "yield",
                SavingsEntry.source_type == "cdi",
            )
        ).all()
        assert len(yields) == 1


def test_yield_uses_previous_day_closing_balance():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    today = date(2026, 5, 26)
    with Session(engine) as session:
        group = SavingsGroup(name="Reserva", cdi_pct=100.0)
        session.add(group)
        session.commit()
        session.refresh(group)
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=(today - timedelta(days=1)).isoformat(),
                amount=100.0,
                direction="deposit",
                source_type="manual",
            )
        )
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=today.isoformat(),
                amount=100.0,
                direction="deposit",
                source_type="manual",
            )
        )
        _insert_cdi(session, today, 0.05)
        session.commit()

        generate_cdi_yields(session, today=today)
        yield_entry = session.exec(
            select(SavingsEntry).where(
                SavingsEntry.group_id == group.id,
                SavingsEntry.direction == "yield",
                SavingsEntry.source_type == "cdi",
            )
        ).one()
        # 100 (yesterday closing) * 0.05% = 0.05
        assert round(yield_entry.amount, 8) == 0.05


def test_generation_continues_from_day_after_last_rendimento():
    engine = _engine()
    SQLModel.metadata.create_all(engine)
    today = date(2026, 5, 27)
    with Session(engine) as session:
        group = SavingsGroup(name="Reserva", cdi_pct=100.0)
        session.add(group)
        session.commit()
        session.refresh(group)
        day1 = today - timedelta(days=1)
        day2 = today
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=(day1 - timedelta(days=1)).isoformat(),
                amount=500.0,
                direction="deposit",
                source_type="manual",
            )
        )
        session.add(
            SavingsEntry(
                group_id=group.id,
                entry_date=day1.isoformat(),
                amount=0.25,
                direction="yield",
                source_type="cdi",
                source_ref_id=day1.isoformat(),
            )
        )
        _insert_cdi(session, day1, 0.05)
        _insert_cdi(session, day2, 0.05)
        session.commit()

        result = generate_cdi_yields(session, today=today)
        assert result["created_rows"] == 1
        yields = session.exec(
            select(SavingsEntry).where(
                SavingsEntry.group_id == group.id,
                SavingsEntry.direction == "yield",
                SavingsEntry.source_type == "cdi",
            )
        ).all()
        assert len(yields) == 2
        assert any(row.entry_date == day2.isoformat() for row in yields)
