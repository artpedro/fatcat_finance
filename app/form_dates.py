"""Parse and format dates for HTML date inputs (value is always YYYY-MM-DD)."""

from __future__ import annotations

from datetime import date, datetime


def month_year_to_date_iso(year: int, month_0_11: int, day: int = 1) -> str:
    return date(year, month_0_11 + 1, day).isoformat()


def parse_iso_date_to_month_year(raw: str) -> tuple[int, int]:
    """
    Accept YYYY-MM-DD (from type=date) or legacy YYYY-MM (month field).
    Returns (month_0_11, year).
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("Informe a data.")
    if len(s) == 7 and s[4] == "-":
        y_str, m_str = s.split("-", 1)
        y, m = int(y_str), int(m_str)
        if not 1 <= m <= 12:
            raise ValueError("Mês inválido.")
        return m - 1, y
    d = datetime.strptime(s[:10], "%Y-%m-%d").date()
    return d.month - 1, d.year
