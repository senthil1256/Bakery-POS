from decimal import Decimal

import pytest

from app.errors import ValidationError
from app.models import OrderLine
from app.money import format_money, to_cents
from app.pricing import calculate_totals, line_total_cents


def line(price, qty=1, discount="0"):
    return OrderLine(1, 1, "x", "c", price, Decimal(discount), qty)


def totals(lines, dine_in=True):
    return calculate_totals(lines, service_charge_rate=Decimal("0.10"),
                            gst_rate=Decimal("0.09"), apply_service_charge=dine_in)


@pytest.mark.parametrize("text,cents", [("4.50", 450), ("4", 400), (4.5, 450), ("0.05", 5), (" 12.00 ", 1200)])
def test_to_cents_valid(text, cents):
    assert to_cents(text) == cents


@pytest.mark.parametrize("bad", ["abc", "", "nan", "inf", "4.505", True, None])
def test_to_cents_rejects_junk(bad):
    # the original isfloat() accepted "nan" and "inf"
    with pytest.raises(ValidationError):
        to_cents(bad)


def test_format_money():
    assert format_money(450) == "$4.50"
    assert format_money(5) == "$0.05"
    assert format_money(123456) == "$1,234.56"
    assert format_money(-250) == "-$2.50"


def test_discount_is_applied():
    # 2 x $4.00 with 10% off = $7.20 (the original stored discounts but never applied them)
    assert line_total_cents(400, Decimal("10"), 2) == 720


def test_half_cent_rounds_up():
    assert line_total_cents(125, Decimal("10"), 1) == 113  # 112.5 -> 113


def test_dine_in_matches_original_formula():
    t = totals([line(1000)])  # original: 10.00 * 1.10 * 1.09 = 11.99
    assert (t.subtotal_cents, t.service_charge_cents, t.gst_cents, t.total_cents) == (1000, 100, 99, 1199)


def test_takeaway_has_no_service_charge():
    t = totals([line(1000)], dine_in=False)
    assert (t.service_charge_cents, t.gst_cents, t.total_cents) == (0, 90, 1090)
    assert t.service_charge_rate == 0


def test_parts_always_add_up_to_total():
    for price in range(1, 2000, 7):
        t = totals([line(price)])
        assert t.total_cents == t.subtotal_cents + t.service_charge_cents + t.gst_cents
