"""Money helpers.

Every amount inside the app is an integer number of cents. Floats are never used
for money, so totals can never drift (0.1 + 0.2 != 0.3 is a float problem, not ours).
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import ValidationError

_ONE = Decimal(1)


def to_cents(value) -> int:
    """Convert a dollar amount such as "4.50", 4.5 or Decimal("4.5") to 450.

    Used where a human types a price. Rejects junk, NaN/inf and more than 2 decimals.
    """
    if isinstance(value, bool):
        raise ValidationError("Amount must be a number")
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(f"'{value}' is not a valid amount") from None
    if not amount.is_finite():
        raise ValidationError(f"'{value}' is not a valid amount")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValidationError("Amounts can have at most 2 decimal places")
    return int(cents)


def round_cents(amount: Decimal) -> int:
    """Round a Decimal number of cents to a whole cent (0.5 rounds up, like a calculator)."""
    return int(amount.quantize(_ONE, rounding=ROUND_HALF_UP))


def format_money(cents: int, symbol: str = "$") -> str:
    """450 -> '$4.50'"""
    sign = "-" if cents < 0 else ""
    dollars, rest = divmod(abs(cents), 100)
    return f"{sign}{symbol}{dollars:,}.{rest:02d}"
