from datetime import date

import pytest

from app.form_dates import (
    date_to_br,
    month_year_to_date_br,
    parse_br_date,
    parse_br_date_to_month_year,
)


def test_parse_br_date_dd_mm_yyyy():
    assert parse_br_date("07/05/2026") == date(2026, 5, 7)


def test_parse_br_date_two_digit_year_expanded():
    assert parse_br_date("01/01/26") == date(2026, 1, 1)


def test_parse_br_date_iso_fallback():
    assert parse_br_date("2026-05-07") == date(2026, 5, 7)


def test_parse_br_date_iso_yyyy_mm_legacy():
    assert parse_br_date("2026-05") == date(2026, 5, 1)


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "32/01/2026", "01/13/2026", "01-02-2026", "1/2/2026/extra"],
)
def test_parse_br_date_rejects_garbage(raw: str):
    with pytest.raises(ValueError):
        parse_br_date(raw)


def test_parse_br_date_to_month_year_returns_zero_indexed_month():
    assert parse_br_date_to_month_year("15/03/2026") == (2, 2026)


def test_round_trip_month_year():
    rendered = month_year_to_date_br(2026, 4, 7)
    assert rendered == "07/05/2026"
    assert parse_br_date(rendered) == date(2026, 5, 7)


def test_date_to_br_pads_zeros():
    assert date_to_br(date(2026, 1, 9)) == "09/01/2026"
