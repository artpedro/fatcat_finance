# FatCat Parity Checklist

This checklist captures behavior from `fatcat.html` plus approved scope changes for the reimplementation.

## Core monthly flow
- [ ] Selected month/year controls all totals and list projections.
- [ ] Dashboard shows total income, card costs, PIX/subscription costs, and remaining balance.
- [ ] Remaining balance = monthly income - card costs - PIX costs.

## Income sources (new)
- [ ] Income is managed in a dedicated table (`income_sources`) with CRUD.
- [ ] Each income source supports recurring and bounded validity windows.
- [ ] Monthly income aggregates all active sources for selected month.

## Cards
- [ ] Card CRUD supports name, closing day, due day, color, limit, maintenance rule.
- [ ] Maintenance fee supports `none`, `fixed`, `conditional`.
- [ ] `is_used_by_subscriptions` is managed for cards with active linked subscriptions.
- [ ] Card totals include expenses, applicable maintenance fee, and card-paid subscriptions.

## Expenses
- [ ] Expense CRUD supports `credit` and `debit` types.
- [ ] Both types are card-linked (`card_id` required).
- [ ] `credit` supports installments with billing cycle projection.
- [ ] `debit` is charged directly in selected month and uses one installment.
- [ ] Expense statuses reflect upcoming/active/done/installment progress.

## Billing cycle and installments
- [ ] If purchase day is after card closing day, billing starts next month.
- [ ] Installment amount is `amount_total / installments`.
- [ ] Monthly projection includes matching installment windows only.

## PIX and subscriptions
- [ ] PIX CRUD supports one-off and recurring entries.
- [ ] Recurring PIX entries apply from start month onward.
- [ ] Subscriptions live in dedicated table and support:
  - [ ] recurring indefinite monthly charges
  - [ ] short-lived charges ending by date or duration
  - [ ] payment method `card` or `pix`
  - [ ] linked card when method is `card`

## Dashboard charts and visuals
- [ ] Doughnut chart by payment source/card.
- [ ] Doughnut chart by category.
- [ ] Sankey flow from income to spending groups and balance.
- [ ] Due-date panel displays urgency by card due day.

## UX/workflow
- [ ] Sidebar navigation preserves the same visual style and section structure.
- [ ] CRUD is performed through HTMX-powered forms/partials.
- [ ] Theme and selected month/year persist.

## Future compatibility
- [ ] Schema and services allow savings groups later with no breaking changes.
