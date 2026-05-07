"""Parse and format dates for the BRL UI (display/input is dd/mm/yyyy).

The forms surface dates in the Brazilian format `dd/mm/yyyy` to keep behavior
consistent across browsers/locales (the native HTML `type="date"` element
displays in the OS locale, which is unpredictable).

This module centralizes:
- Formatting a stored (year, month_0_11, day) tuple into `dd/mm/yyyy`.
- Parsing a `dd/mm/yyyy` string (with legacy fallbacks for `yyyy-mm-dd`,
  `yyyy-mm`) back into either `(month_0_11, year)` or a full `date`.
"""

from __future__ import annotations

from datetime import date, datetime


def month_year_to_date_br(year: int, month_0_11: int, day: int = 1) -> str:
    """Render a stored month/year (with optional day) as `dd/mm/yyyy`."""
    d = date(year, month_0_11 + 1, day)
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"


def date_to_br(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year:04d}"


def parse_br_date(raw: str) -> date:
    """Parse a `dd/mm/yyyy` string into a `date`.

    Accepts ISO `yyyy-mm-dd` as a fallback so legacy stored values keep working.
    Raises `ValueError` with a Portuguese message on invalid input.
    """
    s = (raw or "").strip()
    if not s:
        raise ValueError("Informe a data.")
    if "/" in s:
        parts = s.split("/")
        if len(parts) != 3:
            raise ValueError("Data inválida. Use dd/mm/aaaa.")
        try:
            day, month, year = (int(p) for p in parts)
        except ValueError as exc:
            raise ValueError("Data inválida. Use dd/mm/aaaa.") from exc
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError as exc:
            raise ValueError("Data inválida. Use dd/mm/aaaa.") from exc
    if "-" in s and len(s) >= 7:
        head = s[:10]
        try:
            return datetime.strptime(head if len(head) == 10 else head + "-01", "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("Data inválida. Use dd/mm/aaaa.") from exc
    raise ValueError("Data inválida. Use dd/mm/aaaa.")


def parse_br_date_to_month_year(raw: str) -> tuple[int, int]:
    """Parse a `dd/mm/yyyy` (or legacy ISO) string into `(month_0_11, year)`."""
    d = parse_br_date(raw)
    return d.month - 1, d.year


# Backwards-compatible aliases (legacy callers expected ISO).
def month_year_to_date_iso(year: int, month_0_11: int, day: int = 1) -> str:
    return month_year_to_date_br(year, month_0_11, day)


def parse_iso_date_to_month_year(raw: str) -> tuple[int, int]:
    return parse_br_date_to_month_year(raw)
