"""Plain data structures. No logic lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class OrderStatus(str, Enum):
    OPEN = "open"
    PAID = "paid"
    VOIDED = "voided"


class PaymentMethod(str, Enum):
    CASH = "cash"
    MASTER_CARD = "master_card"
    VISA = "visa"
    AMERICAN_EXPRESS = "american_express"


PAYMENT_LABELS = {
    PaymentMethod.CASH: "Cash",
    PaymentMethod.MASTER_CARD: "Mastercard",
    PaymentMethod.VISA: "Visa",
    PaymentMethod.AMERICAN_EXPRESS: "American Express",
}


@dataclass
class Settings:
    """Shop configuration. Change these to suit the bakery."""
    business_name: str = "Sweet Crumbs Bakery"
    currency_symbol: str = "$"
    gst_rate: Decimal = Decimal("0.09")
    service_charge_rate: Decimal = Decimal("0.10")  # dine-in (table) orders only
    table_count: int = 5


@dataclass
class MenuItem:
    id: int
    name: str
    category: str
    price_cents: int
    discount_percent: Decimal = Decimal("0")
    available: bool = True  # False = sold out for today


@dataclass
class OrderLine:
    """One line on an order. Name and price are copied from the menu at the moment the
    item is added, so editing or deleting a menu item never changes an existing order."""
    line_id: int
    item_id: int
    name: str
    category: str
    unit_price_cents: int
    discount_percent: Decimal
    quantity: int


@dataclass
class Totals:
    subtotal_cents: int          # after item discounts, before service charge and GST
    discount_cents: int          # how much the item discounts took off (information only)
    service_charge_cents: int
    gst_cents: int
    total_cents: int
    service_charge_rate: Decimal  # rate actually applied (0 for takeaway)
    gst_rate: Decimal


@dataclass
class Payment:
    method: PaymentMethod
    amount_cents: int    # what the sale was worth
    tendered_cents: int  # what the customer handed over (== amount for cards)
    change_cents: int


@dataclass
class Order:
    id: int
    table: int | None    # None = takeaway / counter sale
    pax: int
    created_at: str
    status: OrderStatus = OrderStatus.OPEN
    lines: list[OrderLine] = field(default_factory=list)
    next_line_id: int = 1
    paid_at: str | None = None
    payment: Payment | None = None
    totals: Totals | None = None  # frozen when paid, so later rate changes can't alter history
    void_reason: str = ""
    closed_in: int | None = None  # number of the end-of-day report that included this order


@dataclass
class State:
    """Everything the app remembers."""
    menu: dict[int, MenuItem] = field(default_factory=dict)
    orders: dict[int, Order] = field(default_factory=dict)
    z_reports: list[dict] = field(default_factory=list)
    next_item_id: int = 1
    next_order_id: int = 1
