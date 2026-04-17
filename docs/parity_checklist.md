# FatCat Parity Checklist

This checklist reflects the cycle-based rewrite. Items marked ~~strikethrough~~ are intentionally deprecated in favor of the cycle-based
behavior listed alongside.

## Core flow (cycle-based)
- [x] Selected month/year represents a **cycle-end month**; all totals and list projections derive from it.
- [x] `resolve_and_sync_period()` calls `materialize_closed_cycles()` on every request so `BillCycle`/`BillCycleLine` rows stay in sync.
- [x] Dashboard shows: total income, card fatura total (closed + open), PIX avulso, PIX subscriptions, remaining balance.
- [x] Remaining balance = income - card faturas - PIX (adhoc + subscriptions) for the selected cycle.

## Income sources
- [x] Income is managed in a dedicated table (`income_sources`) with CRUD.
- [x] Each income source supports recurring and bounded validity windows.
- [x] Monthly income aggregates all active sources for the selected month (calendar month; income is not cycle-re-anchored).

## Cards
- [x] Card CRUD supports name, closing day, due day, color, limit, maintenance rule.
- [x] Maintenance fee supports `none`, `fixed`, `conditional` and appears as a `maintenance` line in materialized bills.
- [x] `is_used_by_subscriptions` is managed for cards with active linked subscriptions.
- [x] Per-card view renders the active cycle total, any `closed_unpaid` bill, a pay button, and an alarm pill with days-to-Vencimento.
- [x] Each card exposes a bill history partial via `GET /cards/{id}/bills`.

## Bill lifecycle (new)
- [x] `BillCycle` rows are created per card (and per PIX pseudo-flow if enabled) for each cycle-end month.
- [x] `BillCycleLine` rows freeze the content of a cycle when it transitions away from `open`.
- [x] Unpaid closed cycles roll forward into the next open cycle as a single `carryover` line (refreshed on materialize).
- [x] Pay/unpay endpoints freeze/unfreeze the line snapshot and update status + `paid_at`.

## Expenses
- [x] Expense CRUD supports `credit` and `debit` types.
- [x] Both types are card-linked (`card_id` required).
- [x] `credit` supports installments; each installment lands in the cycle `cycle_end_for_purchase(closing, day, month, year) + n`.
- [x] `debit` lands in the single cycle matching its purchase date.
- [x] Expense statuses reflect cycle membership: "Neste ciclo", "Concluído", "Aguardando ciclo", installment progress (`n/N`).

## Billing cycle math
- [x] ~~If purchase day is after closing day, billing starts next month~~ → replaced by cycle math: charges roll into the cycle whose
  `cycle_end_for_purchase` result contains them. Closing day 0 collapses cycles to calendar months.
- [x] Installment amount is `amount_total / installments`; projection spans `installments` consecutive cycles.
- [x] Cycle filter (`period=month`) on the expenses table includes only the selected cycle.

## PIX and subscriptions
- [x] PIX CRUD supports one-off and recurring entries.
- [x] Recurring PIX entries apply from start month onward.
- [x] Subscriptions live in dedicated table and support:
  - [x] recurring indefinite monthly charges
  - [x] short-lived charges ending by date or duration
  - [x] payment method `card` or `pix`
  - [x] linked card when method is `card`
- [x] Subscription active-status uses `subscription_cycle_hit` (cycle-aware) instead of calendar-month membership.
- [x] When `AppSettings.pix_closing_day > 0`, PIX generates real `BillCycle` rows with carryovers; otherwise PIX stays month-bound.

## Dashboard charts and visuals
- [x] Doughnut chart by payment source/card (reads cycle totals).
- [x] Doughnut chart by category (reads `BillCycleLine.category_name_snapshot` for closed bills; live category_names for the open
  cycle).
- [x] Sankey flow from income to spending groups and balance.
- [x] Due-date panel displays urgency by each card's Vencimento for the selected cycle-end month.

## UX / workflow
- [x] Sidebar navigation preserves the same visual style and section structure.
- [x] Sidebar month label reads as "Ciclo de referência"; arrows move between cycles.
- [x] Sidebar exposes the PIX closing-day control (`POST /settings/pix-cycle`).
- [x] CRUD is performed through HTMX-powered forms/partials.
- [x] Theme, selected cycle-end month/year, and `pix_closing_day` persist in `AppSettings`.

## Deprecated behaviors
- [x] ~~`expenses_for_month` as month-based projection~~ remains only as a thin shim delegating to `expenses_for_cycle`.
- [x] ~~`billing_start`~~ kept as back-compat alias for `cycle_end_for_purchase`.
- [x] ~~`db_migrate.py`~~ removed: no migrations; fresh-start policy, wipe `fatcat.db` on upgrade.

## Future compatibility
- [x] Schema and services allow savings groups later with no breaking changes.
- [x] `BillCycle.carryover_from_id` is reserved for richer overdue cascading in future iterations.
