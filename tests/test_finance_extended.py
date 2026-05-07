"""Edge cases: multiple cards, closing-day quirks (Feb/31), assinaturas, PIX."""

from __future__ import annotations

from app.models import Card, Expense, PixItem, Subscription
from app.services.finance import (
    cycle_bounds,
    cycle_end_for_purchase,
    expenses_for_cycle,
    lines_for_open_cycle,
    lines_for_open_pix_cycle,
    subscription_charge_date,
    subscription_costs_by_method,
    subscription_cycle_hit,
    pix_cycle_hit,
)

_CID = "a" * 32


def _expense(cid: str, **kwargs):
    defaults = dict(
        type="debit",
        card_id=cid,
        description="x",
        amount_total=100,
        installments=1,
        purchase_day=1,
        purchase_month=0,
        purchase_year=2026,
        category_id=_CID,
    )
    defaults.update(kwargs)
    return Expense(**defaults)


def test_same_calendar_purchase_differs_by_card_when_closings_split_march_boundary():
    """March 10: early closing (dia 5) pushes to Abril; late closing (dia 25) stays in Março fatura."""
    card_early_close = Card(name="EarlyClose", closing_day=5)
    card_late_close = Card(name="LateClose", closing_day=25)
    e_early = _expense(
        card_early_close.id,
        description="Mercado-A",
        purchase_day=10,
        purchase_month=2,
        purchase_year=2026,
        card_id=card_early_close.id,
    )
    e_late = _expense(
        card_late_close.id,
        description="Mercado-B",
        purchase_day=10,
        purchase_month=2,
        purchase_year=2026,
        card_id=card_late_close.id,
    )
    cmap = {
        card_early_close.id: card_early_close,
        card_late_close.id: card_late_close,
    }
    march = expenses_for_cycle([e_early, e_late], cmap, 2, 2026)
    april = expenses_for_cycle([e_early, e_late], cmap, 3, 2026)

    march_ids = {r["expense"].card_id for r in march}
    april_ids = {r["expense"].card_id for r in april}

    assert card_late_close.id in march_ids
    assert card_early_close.id not in march_ids
    assert card_early_close.id in april_ids
    assert card_late_close.id not in april_ids


def test_two_cards_credit_installments_stay_partitioned():
    """Same cycle label; installments only attach to owning card."""
    c1 = Card(name="Card1", closing_day=10)
    c2 = Card(name="Card2", closing_day=10)
    ex1 = _expense(
        c1.id,
        type="credit",
        card_id=c1.id,
        amount_total=300,
        installments=3,
        purchase_day=1,
        purchase_month=0,
        purchase_year=2026,
        description="Cred-A",
    )
    ex2 = _expense(
        c2.id,
        type="credit",
        card_id=c2.id,
        amount_total=600,
        installments=6,
        purchase_day=1,
        purchase_month=0,
        purchase_year=2026,
        description="Cred-B",
    )
    cmap = {c1.id: c1, c2.id: c2}
    jan = expenses_for_cycle([ex1, ex2], cmap, 0, 2026)
    mar = expenses_for_cycle([ex1, ex2], cmap, 2, 2026)
    assert {r["expense"].card_id for r in jan} == {c1.id, c2.id}
    assert all(r["expense"].card_id != c2.id or r["inst_num"] == 3 for r in mar)


def test_feb29_leap_purchase_crosses_february_closure():
    """Fechamento 28 em ano bissexto: dia 29/02 fecha no ciclo de Março."""
    assert cycle_end_for_purchase(28, 29, 1, 2024) == (2, 2024)


def test_closing_day_31_february_cycles_use_clamped_end():
    _, sm, sy, ed, em, ey = cycle_bounds(31, 1, 2026)
    assert (em, ey) == (1, 2026)
    assert ed == 28


def test_closing_day_31_april_clamps_end_to_30():
    """Fim de ciclo em Abril com fechamento 31 → usa o último dia real do mês."""
    sd, sm, sy, ed, em, ey = cycle_bounds(31, 3, 2026)
    assert (ed, em, ey) == (30, 3, 2026)
    assert (sm, sy) == (3, 2026)
    assert sd >= 1


def test_credit_installments_span_calendar_year_boundary():
    """Parcelas seguem ciclo ordinal (chave mensal), incluindo virada de ano."""
    card = Card(name="Visa", closing_day=0)
    ex = Expense(
        type="credit",
        card_id=card.id,
        description="Black",
        amount_total=240,
        installments=4,
        purchase_day=1,
        purchase_month=10,
        purchase_year=2025,
        category_id=_CID,
    )
    cmap = {card.id: card}
    nov = expenses_for_cycle([ex], cmap, 10, 2025)
    jan = expenses_for_cycle([ex], cmap, 0, 2026)
    assert len(nov) == 1 and nov[0]["inst_num"] == 1
    assert len(jan) == 1 and jan[0]["inst_num"] == 3
    dec_row = expenses_for_cycle([ex], cmap, 11, 2025)
    assert dec_row and dec_row[0]["inst_num"] == 2


def test_subscription_billing_day_20_hits_march_for_both_tight_and_loose_closing():
    """Mesmo dia de cobrança: ambos cartões podem acertar a fatura que termina em Março (vigência calendário coerente)."""
    c_tight = Card(name="Tight", closing_day=15)
    c_loose = Card(name="Loose", closing_day=31)
    sub_t = Subscription(
        description="NetA",
        amount_monthly=50,
        billing_day=20,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=c_tight.id,
        category_id=_CID,
    )
    sub_l = Subscription(
        description="NetB",
        amount_monthly=50,
        billing_day=20,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=c_loose.id,
        category_id=_CID,
    )

    assert subscription_cycle_hit(sub_t, 15, 2, 2026) is True
    assert subscription_cycle_hit(sub_l, 31, 2, 2026) is True
    assert subscription_cycle_hit(sub_t, 15, 3, 2026) is True


def test_subscription_billing_after_effective_closing_targets_previous_calendar_month():
    """Cobrança dia 28 com fechamento 15 no fim do ciclo em Março → olha Fev para vigência."""
    card = Card(name="Nubank", closing_day=15)
    sub = Subscription(
        description="Late bill",
        amount_monthly=99,
        billing_day=28,
        start_month=1,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=card.id,
        category_id=_CID,
    )
    assert subscription_cycle_hit(sub, 15, 2, 2026) is True
    d, m, y = subscription_charge_date(sub, 15, 2, 2026)
    assert (d, m, y) == (28, 1, 2026)


def test_subscription_charge_date_clamps_day_31_in_february():
    sub = Subscription(
        description="Rent",
        amount_monthly=400,
        billing_day=31,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id="card1",
        category_id=_CID,
    )
    d, m, y = subscription_charge_date(sub, 10, 1, 2026)
    assert m == 0
    assert d == 31

    d2, m2, y2 = subscription_charge_date(sub, 10, 1, 2025)
    assert (m2, y2) == (0, 2025)
    assert d2 == 31


def test_subscription_costs_by_method_splits_card_vs_pix_with_mixed_closings():
    c1 = Card(id="c1111111111111111111111111111111", name="A", closing_day=10)
    c2 = Card(id="c2222222222222222222222222222222", name="B", closing_day=25)
    s_card = Subscription(
        description="Card sub",
        amount_monthly=30,
        billing_day=5,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=c1.id,
        category_id=_CID,
    )
    s_pix = Subscription(
        description="Pix sub",
        amount_monthly=19.9,
        billing_day=8,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="pix",
        card_id=None,
        category_id=_CID,
    )
    ghost = Subscription(
        description="Other card",
        amount_monthly=40,
        billing_day=5,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=c2.id,
        category_id=_CID,
    )
    cmap = {c1.id: 10, c2.id: 25}
    card_items, pix_items = subscription_costs_by_method(
        [s_card, s_pix, ghost],
        2,
        2026,
        card_closing_map=cmap,
        pix_closing_day=12,
    )
    assert s_card in card_items
    assert s_pix in pix_items
    assert ghost in card_items


def test_lines_for_open_cycle_excludes_other_cards_subscriptions():
    c1 = Card(name="Mine", closing_day=10)
    c2 = Card(name="Theirs", closing_day=10)
    only_c2 = Subscription(
        description="Not mine",
        amount_monthly=50,
        billing_day=5,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="card",
        card_id=c2.id,
        category_id=_CID,
    )
    lines = lines_for_open_cycle(
        card=c1,
        end_month=2,
        end_year=2026,
        expenses=[],
        subscriptions=[only_c2],
        category_names={},
    )
    assert not any(line["kind"] == "subscription" for line in lines)


def test_lines_for_open_pix_cycle_merges_pix_items_and_pix_subscriptions():
    pix_one = PixItem(
        description="Doação",
        amount=50,
        start_month=2,
        start_year=2026,
        is_recurring=False,
        category_id=_CID,
    )
    pix_rec = PixItem(
        description="Pool",
        amount=80,
        start_month=0,
        start_year=2026,
        is_recurring=True,
        category_id=_CID,
    )
    sub_pix = Subscription(
        description="Spotify",
        amount_monthly=21.9,
        billing_day=12,
        start_month=0,
        start_year=2026,
        is_indefinite=True,
        payment_method="pix",
        card_id=None,
        category_id=_CID,
    )
    lines = lines_for_open_pix_cycle(
        end_month=2,
        end_year=2026,
        pix_closing_day=15,
        pix_items=[pix_one, pix_rec],
        subscriptions=[sub_pix],
        category_names={_CID: "Lazer"},
    )
    kinds = {line["kind"] for line in lines}
    assert kinds == {"pix", "subscription"}
    assert sum(line["amount"] for line in lines if line["kind"] == "pix") == 50 + 80
    assert any(line["description"] == "Spotify" for line in lines)


def test_pix_cycle_hit_recurring_independent_of_pix_closing_flag():
    """Sem dia de carga no PixItem, recorrente entra em todo ciclo >= início (doc atual)."""
    pix = PixItem(
        description="Net",
        amount=10,
        start_month=5,
        start_year=2026,
        is_recurring=True,
        category_id=_CID,
    )
    assert pix_cycle_hit(pix, 0, 5, 2026) is True
    assert pix_cycle_hit(pix, 99, 5, 2026) is True


def test_pix_one_off_only_matches_exact_cycle_key():
    pix = PixItem(
        description="Once",
        amount=200,
        start_month=4,
        start_year=2026,
        is_recurring=False,
        category_id=_CID,
    )
    assert pix_cycle_hit(pix, 0, 4, 2026) is True
    assert pix_cycle_hit(pix, 0, 5, 2026) is False


def test_subscription_cycle_hit_pix_closing_zero_is_calendar_month():
    sub = Subscription(
        description="Cloud",
        amount_monthly=15,
        billing_day=1,
        start_month=2,
        start_year=2026,
        is_indefinite=True,
        payment_method="pix",
        card_id=None,
        category_id=_CID,
    )
    assert subscription_cycle_hit(sub, 0, 2, 2026) is True
    assert subscription_cycle_hit(sub, 0, 1, 2026) is False
