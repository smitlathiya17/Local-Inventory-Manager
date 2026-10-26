from decimal import Decimal, ROUND_HALF_UP
from datetime import date as _date


def grams_to_kg_str(grams: int) -> str:
    """2500 → '2.500'"""
    kg = Decimal(grams) / 1000
    return f"{kg:.3f}"


def kg_str_to_grams(kg_str: str) -> int:
    """'2.5' → 2500. Raises ValueError on bad input."""
    try:
        d = Decimal(str(kg_str).strip().replace(',', ''))
    except Exception:
        raise ValueError(f"Invalid weight: {kg_str!r}")
    if d < 0:
        raise ValueError("Weight cannot be negative")
    return int((d * 1000).to_integral_value(rounding=ROUND_HALF_UP))


def paise_to_rupees_str(paise: int) -> str:
    """482500 → '₹4,82,500'"""
    rupees = paise // 100
    paise_part = paise % 100
    if paise_part:
        return f"₹{_indian_format(rupees)}.{paise_part:02d}"
    return f"₹{_indian_format(rupees)}"


def rupee_str_to_paise(rupee_str: str) -> int:
    """'550.50' or '₹550' → paise int. Raises ValueError on bad input."""
    try:
        d = Decimal(str(rupee_str).strip().lstrip('₹').replace(',', ''))
    except Exception:
        raise ValueError(f"Invalid amount: {rupee_str!r}")
    if d < 0:
        raise ValueError("Amount cannot be negative")
    return int((d * 100).to_integral_value(rounding=ROUND_HALF_UP))


def calc_line_total_paise(price_per_kg_paise: int, grams: int) -> int:
    """Round-half-up: price_per_kg_paise × grams / 1000."""
    result = Decimal(price_per_kg_paise) * Decimal(grams) / Decimal(1000)
    return int(result.to_integral_value(rounding=ROUND_HALF_UP))


def _indian_format(n: int) -> str:
    """482500 → '4,82,500'"""
    sign = '-' if n < 0 else ''
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    result = s[-3:]
    s = s[:-3]
    while s:
        result = s[-2:] + ',' + result
        s = s[:-2]
    return sign + result.lstrip(',')


def format_date(iso_date: str) -> str:
    """'2026-10-26' → '26 Oct 2026'"""
    d = _date.fromisoformat(iso_date)
    return f"{d.day} {d.strftime('%b')} {d.year}"


def today_iso() -> str:
    return _date.today().isoformat()
