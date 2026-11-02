"""All price maths lives here, in one place, so a receipt and a report can never disagree."""
from __future__ import annotations

from decimal import Decimal

from .models import OrderLine, Totals
from .money import round_cents

_HUNDRED = Decimal(100)


def line_total_cents(unit_price_cents: int, discount_percent: Decimal, quantity: int) -> int:
    """Price of a line after its item discount, rounded to a whole cent."""
    gross = Decimal(unit_price_cents * quantity)
    return round_cents(gross * (_HUNDRED - discount_percent) / _HUNDRED)


def calculate_totals(
    lines: list[OrderLine],
    *,
    service_charge_rate: Decimal,
    gst_rate: Decimal,
    apply_service_charge: bool,
) -> Totals:
    """Subtotal -> service charge -> GST (charged on subtotal + service charge).

    Each part is rounded to a whole cent on its own, so the parts printed on the
    receipt always add up exactly to the printed total.
    """
    gross = sum(l.unit_price_cents * l.quantity for l in lines)
    subtotal = sum(line_total_cents(l.unit_price_cents, l.discount_percent, l.quantity) for l in lines)
    rate = service_charge_rate if apply_service_charge else Decimal(0)
    service = round_cents(Decimal(subtotal) * rate)
    gst = round_cents(Decimal(subtotal + service) * gst_rate)
    return Totals(
        subtotal_cents=subtotal,
        discount_cents=gross - subtotal,
        service_charge_cents=service,
        gst_cents=gst,
        total_cents=subtotal + service + gst,
        service_charge_rate=rate,
        gst_rate=gst_rate,
    )
