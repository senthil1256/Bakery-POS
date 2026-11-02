"""The POS business logic.

Rules of this module:
  * No input(), print() or sleep(). Anything can call it: a web API, a test, a CLI.
  * Every amount is integer cents.
  * Every public method returns plain JSON-friendly dicts/lists.
  * Every problem raises a POSError subclass with a message the cashier can read.
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from .errors import ConflictError, NotFoundError, ValidationError
from .models import (PAYMENT_LABELS, MenuItem, Order, OrderLine, OrderStatus,
                     Payment, PaymentMethod, Settings, State, Totals)
from .money import format_money, round_cents
from .pricing import calculate_totals, line_total_cents
from .storage import Storage

MAX_PRICE_CENTS = 1_000_000   # $10,000.00, a typo guard
MAX_LINE_QUANTITY = 999


# ---------- small validators ----------

def _require_int(value, label: str, minimum: int | None = None, maximum: int | None = None) -> int:
    ok = isinstance(value, int) and not isinstance(value, bool)
    if ok and (minimum is None or value >= minimum) and (maximum is None or value <= maximum):
        return value
    if minimum is not None and maximum is not None:
        raise ValidationError(f"{label} must be a whole number between {minimum} and {maximum}")
    if minimum is not None:
        raise ValidationError(f"{label} must be a whole number of at least {minimum}")
    raise ValidationError(f"{label} must be a whole number")


def _clean_text(value, label: str, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    cleaned = " ".join(value.split())  # trims and collapses repeated spaces
    if not cleaned:
        raise ValidationError(f"{label} cannot be empty")
    if len(cleaned) > max_len:
        raise ValidationError(f"{label} must be at most {max_len} characters")
    return cleaned


def _clean_discount(value) -> Decimal:
    message = "Discount must be a number between 0 and 100"
    if isinstance(value, bool):
        raise ValidationError(message)
    try:
        discount = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(message) from None
    if not discount.is_finite() or discount < 0 or discount > 100:
        raise ValidationError(message)
    return discount


def _percent(value: Decimal) -> str:
    """Decimal('10.00') -> '10'. Used for showing percentages."""
    return format(value.normalize(), "f")


class PosService:
    def __init__(self, storage: Storage, settings: Settings | None = None,
                 clock: Callable[[], datetime] = datetime.now) -> None:
        self._storage = storage
        self.settings = settings or Settings()
        self._clock = clock
        self._lock = threading.RLock()  # a web server may call us from several threads
        self._state: State = storage.load() or State()

    # ------------------------------------------------------------------ helpers

    def _now(self) -> str:
        return self._clock().isoformat(timespec="seconds")

    def _save(self) -> None:
        self._storage.save(self._state)

    def _money(self, cents: int) -> str:
        return format_money(cents, self.settings.currency_symbol)

    def _get_item(self, item_id: int) -> MenuItem:
        item = self._state.menu.get(item_id)
        if item is None:
            raise NotFoundError(f"Menu item {item_id} does not exist")
        return item

    def _get_order(self, order_id: int) -> Order:
        order = self._state.orders.get(order_id)
        if order is None:
            raise NotFoundError(f"Order {order_id} does not exist")
        return order

    def _open_order(self, order_id: int) -> Order:
        order = self._get_order(order_id)
        if order.status is not OrderStatus.OPEN:
            raise ConflictError(f"Order #{order.id} is {order.status.value} and can no longer be changed")
        return order

    @staticmethod
    def _get_line(order: Order, line_id: int) -> OrderLine:
        for line in order.lines:
            if line.line_id == line_id:
                return line
        raise NotFoundError(f"Line {line_id} is not on order #{order.id}")

    def _live_totals(self, order: Order) -> Totals:
        return calculate_totals(
            order.lines,
            service_charge_rate=self.settings.service_charge_rate,
            gst_rate=self.settings.gst_rate,
            apply_service_charge=order.table is not None,  # dine-in only
        )

    def _totals_of(self, order: Order) -> Totals:
        return order.totals or self._live_totals(order)

    # ------------------------------------------------------------------ views

    @staticmethod
    def _item_view(item: MenuItem) -> dict:
        return {
            "id": item.id, "name": item.name, "category": item.category,
            "price_cents": item.price_cents,
            "discount_percent": _percent(item.discount_percent),
            "available": item.available,
        }

    def _order_view(self, order: Order) -> dict:
        t = self._totals_of(order)
        payment = None
        if order.payment:
            p = order.payment
            payment = {"method": p.method.value, "label": PAYMENT_LABELS[p.method],
                       "amount_cents": p.amount_cents, "tendered_cents": p.tendered_cents,
                       "change_cents": p.change_cents}
        return {
            "id": order.id,
            "table": order.table,
            "order_type": "takeaway" if order.table is None else "dine_in",
            "pax": order.pax,
            "status": order.status.value,
            "created_at": order.created_at,
            "paid_at": order.paid_at,
            "void_reason": order.void_reason,
            "lines": [{
                "line_id": l.line_id, "item_id": l.item_id, "name": l.name, "category": l.category,
                "unit_price_cents": l.unit_price_cents,
                "discount_percent": _percent(l.discount_percent),
                "quantity": l.quantity,
                "line_total_cents": line_total_cents(l.unit_price_cents, l.discount_percent, l.quantity),
            } for l in order.lines],
            "totals": {
                "subtotal_cents": t.subtotal_cents, "discount_cents": t.discount_cents,
                "service_charge_cents": t.service_charge_cents, "gst_cents": t.gst_cents,
                "total_cents": t.total_cents,
                "service_charge_percent": _percent(t.service_charge_rate * 100),
                "gst_percent": _percent(t.gst_rate * 100),
            },
            "payment": payment,
        }

    # ------------------------------------------------------------------ menu

    def list_menu(self) -> list[dict]:
        """Menu grouped by category, in the order items were created: [{category, items}]."""
        with self._lock:
            groups: dict[str, list[dict]] = {}
            for item in sorted(self._state.menu.values(), key=lambda i: i.id):
                groups.setdefault(item.category, []).append(self._item_view(item))
            return [{"category": c, "items": items} for c, items in groups.items()]

    def _canonical_category(self, category: str, ignore_id: int | None = None) -> str:
        """'bread' and 'Bread' are the same category: reuse the spelling already in use."""
        for item in self._state.menu.values():
            if item.id != ignore_id and item.category.casefold() == category.casefold():
                return item.category
        return category

    def _ensure_name_free(self, name: str, ignore_id: int | None = None) -> None:
        for item in self._state.menu.values():
            if item.id != ignore_id and item.name.casefold() == name.casefold():
                raise ConflictError(f"'{item.name}' is already on the menu")

    def add_menu_item(self, name: str, category: str, price_cents: int,
                      discount_percent=0, available: bool = True) -> dict:
        with self._lock:
            name = _clean_text(name, "Name", 60)
            category = self._canonical_category(_clean_text(category, "Category", 40))
            price_cents = _require_int(price_cents, "Price", 1, MAX_PRICE_CENTS)
            discount = _clean_discount(discount_percent)
            self._ensure_name_free(name)
            item = MenuItem(self._state.next_item_id, name, category, price_cents, discount, bool(available))
            self._state.next_item_id += 1
            self._state.menu[item.id] = item
            self._save()
            return self._item_view(item)

    def update_menu_item(self, item_id: int, *, name: str | None = None, category: str | None = None,
                         price_cents: int | None = None, discount_percent=None,
                         available: bool | None = None) -> dict:
        """Change any of the fields given (None = leave as is). Existing orders are not affected."""
        with self._lock:
            item = self._get_item(item_id)
            new_name = _clean_text(name, "Name", 60) if name is not None else item.name
            new_category = (self._canonical_category(_clean_text(category, "Category", 40), item.id)
                            if category is not None else item.category)
            new_price = (_require_int(price_cents, "Price", 1, MAX_PRICE_CENTS)
                         if price_cents is not None else item.price_cents)
            new_discount = _clean_discount(discount_percent) if discount_percent is not None else item.discount_percent
            self._ensure_name_free(new_name, ignore_id=item.id)
            item.name, item.category, item.price_cents, item.discount_percent = (
                new_name, new_category, new_price, new_discount)
            if available is not None:
                item.available = bool(available)
            self._save()
            return self._item_view(item)

    def remove_menu_item(self, item_id: int) -> dict:
        with self._lock:
            item = self._get_item(item_id)
            del self._state.menu[item_id]
            self._save()
            return self._item_view(item)

    # ------------------------------------------------------------------ orders

    def table_status(self) -> list[dict]:
        with self._lock:
            busy = {o.table: o for o in self._state.orders.values()
                    if o.status is OrderStatus.OPEN and o.table is not None}
            result = []
            for number in range(1, self.settings.table_count + 1):
                order = busy.get(number)
                result.append({
                    "table": number,
                    "occupied": order is not None,
                    "order_id": order.id if order else None,
                    "pax": order.pax if order else 0,
                    "total_cents": self._totals_of(order).total_cents if order else 0,
                })
            return result

    def open_order(self, table: int | None = None, pax: int = 0) -> dict:
        """Start an order. table=None is a takeaway / counter sale."""
        with self._lock:
            if table is not None:
                table = _require_int(table, "Table", 1, self.settings.table_count)
                for o in self._state.orders.values():
                    if o.status is OrderStatus.OPEN and o.table == table:
                        raise ConflictError(f"Table {table} already has an open order (#{o.id})")
                pax = _require_int(pax, "Number of guests", 0, 99)
            else:
                pax = 0
            order = Order(id=self._state.next_order_id, table=table, pax=pax, created_at=self._now())
            self._state.next_order_id += 1
            self._state.orders[order.id] = order
            self._save()
            return self._order_view(order)

    def get_order(self, order_id: int) -> dict:
        with self._lock:
            return self._order_view(self._get_order(order_id))

    def list_open_orders(self) -> list[dict]:
        with self._lock:
            return [self._order_view(o) for o in self._state.orders.values()
                    if o.status is OrderStatus.OPEN]

    def add_item(self, order_id: int, item_id: int, quantity: int = 1) -> dict:
        with self._lock:
            order = self._open_order(order_id)
            item = self._get_item(item_id)
            if not item.available:
                raise ConflictError(f"{item.name} is sold out")
            quantity = _require_int(quantity, "Quantity", 1, MAX_LINE_QUANTITY)
            same = next((l for l in order.lines
                         if l.item_id == item.id and l.unit_price_cents == item.price_cents
                         and l.discount_percent == item.discount_percent), None)
            if same:
                if same.quantity + quantity > MAX_LINE_QUANTITY:
                    raise ValidationError(f"Quantity cannot go above {MAX_LINE_QUANTITY}")
                same.quantity += quantity
            else:
                order.lines.append(OrderLine(order.next_line_id, item.id, item.name, item.category,
                                             item.price_cents, item.discount_percent, quantity))
                order.next_line_id += 1
            self._save()
            return self._order_view(order)

    def set_line_quantity(self, order_id: int, line_id: int, quantity: int) -> dict:
        """Set a line's quantity. 0 removes the line (this is what the +/- buttons use)."""
        with self._lock:
            order = self._open_order(order_id)
            line = self._get_line(order, line_id)
            quantity = _require_int(quantity, "Quantity", 0, MAX_LINE_QUANTITY)
            if quantity == 0:
                order.lines.remove(line)
            else:
                line.quantity = quantity
            self._save()
            return self._order_view(order)

    def remove_line(self, order_id: int, line_id: int) -> dict:
        return self.set_line_quantity(order_id, line_id, 0)

    def void_order(self, order_id: int, reason: str = "") -> dict:
        """Cancel an unpaid order. It is kept for the record, and the table is freed."""
        with self._lock:
            order = self._open_order(order_id)
            order.status = OrderStatus.VOIDED
            order.void_reason = " ".join(str(reason).split())[:100]
            self._save()
            return self._order_view(order)

    def pay_order(self, order_id: int, method: str, tendered_cents: int | None = None) -> dict:
        """Take payment, freeze the totals and free the table.

        Cash needs `tendered_cents` (what the customer handed over); change is worked out here.
        """
        with self._lock:
            order = self._open_order(order_id)
            if not order.lines:
                raise ValidationError("Cannot pay an empty order")
            try:
                payment_method = PaymentMethod(method)
            except ValueError:
                valid = ", ".join(m.value for m in PaymentMethod)
                raise ValidationError(f"Unknown payment method '{method}'. Choose one of: {valid}") from None

            totals = self._live_totals(order)
            if payment_method is PaymentMethod.CASH:
                if tendered_cents is None:
                    raise ValidationError("Enter the cash amount received")
                tendered = _require_int(tendered_cents, "Cash received", 0)
                if tendered < totals.total_cents:
                    raise ValidationError(f"Cash received ({self._money(tendered)}) is less than "
                                          f"the total ({self._money(totals.total_cents)})")
                change = tendered - totals.total_cents
            else:
                tendered, change = totals.total_cents, 0

            order.status = OrderStatus.PAID
            order.paid_at = self._now()
            order.totals = totals
            order.payment = Payment(payment_method, totals.total_cents, tendered, change)
            self._save()
            return self._order_view(order)

    def get_receipt(self, order_id: int) -> dict:
        """Everything a receipt needs. Works for open orders too (shown as a BILL)."""
        with self._lock:
            order = self._get_order(order_id)
            title = {OrderStatus.OPEN: "BILL", OrderStatus.PAID: "RECEIPT",
                     OrderStatus.VOIDED: "VOIDED"}[order.status]
            return {
                "business_name": self.settings.business_name,
                "currency_symbol": self.settings.currency_symbol,
                "receipt_number": f"{order.id:05d}",
                "title": title,
                **self._order_view(order),
            }

    # ------------------------------------------------------------------ reports

    def _unclosed_orders(self) -> list[Order]:
        return [o for o in self._state.orders.values()
                if o.closed_in is None and o.status in (OrderStatus.PAID, OrderStatus.VOIDED)]

    def _build_report(self, orders: list[Order]) -> dict:
        paid = [o for o in orders if o.status is OrderStatus.PAID]
        voided = [o for o in orders if o.status is OrderStatus.VOIDED]

        by_method = {m.value: {"label": PAYMENT_LABELS[m], "count": 0, "amount_cents": 0}
                     for m in PaymentMethod}
        sold: dict[str, dict] = {}
        for o in paid:
            entry = by_method[o.payment.method.value]
            entry["count"] += 1
            entry["amount_cents"] += o.payment.amount_cents
            for l in o.lines:
                row = sold.setdefault(l.name, {"name": l.name, "quantity": 0, "revenue_cents": 0})
                row["quantity"] += l.quantity
                row["revenue_cents"] += line_total_cents(l.unit_price_cents, l.discount_percent, l.quantity)

        total = sum(o.totals.total_cents for o in paid)
        return {
            "orders_paid": len(paid),
            "orders_voided": len(voided),
            "subtotal_cents": sum(o.totals.subtotal_cents for o in paid),
            "discounts_cents": sum(o.totals.discount_cents for o in paid),
            "service_charge_cents": sum(o.totals.service_charge_cents for o in paid),
            "gst_cents": sum(o.totals.gst_cents for o in paid),
            "total_cents": total,
            "average_order_cents": round_cents(Decimal(total) / len(paid)) if paid else 0,
            "by_payment_method": by_method,
            "items_sold": sorted(sold.values(), key=lambda r: (-r["quantity"], r["name"])),
        }

    def sales_report(self) -> dict:
        """Live report of everything sold since the last end-of-day close (an 'X report')."""
        with self._lock:
            return {"generated_at": self._now(), "closed": False,
                    **self._build_report(self._unclosed_orders())}

    def close_day(self) -> dict:
        """End-of-day close (a 'Z report'): saves a permanent numbered report and starts a
        fresh tally. Refuses while any order is still open."""
        with self._lock:
            open_count = sum(1 for o in self._state.orders.values() if o.status is OrderStatus.OPEN)
            if open_count:
                raise ConflictError(f"{open_count} order(s) are still open. Pay or void them before closing")
            orders = self._unclosed_orders()
            if not orders:
                raise ConflictError("There are no sales to close")
            number = len(self._state.z_reports) + 1
            report = {"number": number, "closed_at": self._now(), "closed": True,
                      **self._build_report(orders)}
            for o in orders:
                o.closed_in = number
            self._state.z_reports.append(report)
            self._save()
            return copy.deepcopy(report)

    def list_closed_reports(self) -> list[dict]:
        """All past end-of-day reports, newest first."""
        with self._lock:
            return copy.deepcopy(self._state.z_reports[::-1])
