from datetime import datetime

import pytest

from app.errors import ConflictError, NotFoundError, StorageError, ValidationError
from app.receipt import format_receipt_text
from app.services import PosService
from app.storage import JsonStorage, MemoryStorage


@pytest.fixture
def pos():
    service = PosService(MemoryStorage(), clock=lambda: datetime(2026, 11, 2, 14, 30, 0))
    service.croissant = service.add_menu_item("Croissant", "Pastries", 350)["id"]
    service.latte = service.add_menu_item("Latte", "Drinks", 500)["id"]
    service.danish = service.add_menu_item("Cheese Danish", "Pastries", 400, discount_percent=10)["id"]
    return service


# ---------------------------------------------------------------- menu

def test_menu_is_grouped_by_category(pos):
    menu = pos.list_menu()
    assert [g["category"] for g in menu] == ["Pastries", "Drinks"]
    assert [i["name"] for i in menu[0]["items"]] == ["Croissant", "Cheese Danish"]


def test_names_are_kept_as_typed_not_underscored(pos):
    assert pos.add_menu_item("  Apple   Pie ", "Cakes", 600)["name"] == "Apple Pie"


def test_duplicate_name_rejected_case_insensitive(pos):
    with pytest.raises(ConflictError):
        pos.add_menu_item("croissant", "Pastries", 300)


def test_category_spelling_is_reused(pos):
    assert pos.add_menu_item("Scone", "pastries", 300)["category"] == "Pastries"
    assert len(pos.list_menu()) == 2


@pytest.mark.parametrize("kwargs", [
    dict(name="", category="A", price_cents=100),
    dict(name="X", category="", price_cents=100),
    dict(name="X", category="A", price_cents=0),
    dict(name="X", category="A", price_cents=-5),
    dict(name="X", category="A", price_cents=1.5),
    dict(name="X", category="A", price_cents=100, discount_percent=101),
    dict(name="X", category="A", price_cents=100, discount_percent=-1),
    dict(name="X", category="A", price_cents=100, discount_percent="nan"),
])
def test_invalid_menu_items_rejected(pos, kwargs):
    with pytest.raises(ValidationError):
        pos.add_menu_item(**kwargs)


def test_update_menu_item(pos):
    updated = pos.update_menu_item(pos.latte, price_cents=550, available=False)
    assert updated["price_cents"] == 550 and updated["available"] is False
    with pytest.raises(ConflictError):
        pos.update_menu_item(pos.latte, name="Croissant")


def test_sold_out_item_cannot_be_ordered_but_stays_on_menu(pos):
    pos.update_menu_item(pos.latte, available=False)
    order = pos.open_order()
    with pytest.raises(ConflictError):
        pos.add_item(order["id"], pos.latte)
    assert any(i["name"] == "Latte" for g in pos.list_menu() for i in g["items"])


# ---------------------------------------------------------------- orders

def test_add_items_merges_same_item(pos):
    order = pos.open_order()
    pos.add_item(order["id"], pos.croissant, 2)
    result = pos.add_item(order["id"], pos.croissant)
    assert len(result["lines"]) == 1 and result["lines"][0]["quantity"] == 3


def test_quantity_validation(pos):
    order = pos.open_order()
    for bad in (0, -1, 1.5, "2", None):
        with pytest.raises(ValidationError):
            pos.add_item(order["id"], pos.croissant, bad)


def test_plus_minus_and_remove(pos):
    order = pos.open_order()
    line_id = pos.add_item(order["id"], pos.croissant, 2)["lines"][0]["line_id"]
    assert pos.set_line_quantity(order["id"], line_id, 5)["lines"][0]["quantity"] == 5
    assert pos.set_line_quantity(order["id"], line_id, 0)["lines"] == []
    with pytest.raises(NotFoundError):
        pos.remove_line(order["id"], 999)


def test_unknown_ids(pos):
    with pytest.raises(NotFoundError):
        pos.get_order(404)
    with pytest.raises(NotFoundError):
        pos.add_item(pos.open_order()["id"], 404)


def test_table_rules(pos):
    pos.open_order(table=3, pax=2)
    with pytest.raises(ConflictError):
        pos.open_order(table=3, pax=1)
    for bad in (0, 6, -1, "2"):
        with pytest.raises(ValidationError):
            pos.open_order(table=bad)
    status = {t["table"]: t for t in pos.table_status()}
    assert status[3]["occupied"] and status[3]["pax"] == 2
    assert not status[1]["occupied"] and len(status) == 5


def test_two_takeaway_orders_can_be_open_together(pos):
    assert pos.open_order()["id"] != pos.open_order()["id"]


def test_price_is_frozen_on_the_order(pos):
    order = pos.open_order()
    pos.add_item(order["id"], pos.latte)
    pos.update_menu_item(pos.latte, price_cents=999)
    pos.remove_menu_item(pos.croissant)
    result = pos.add_item(order["id"], pos.latte)  # new price -> separate line
    assert [l["unit_price_cents"] for l in result["lines"]] == [500, 999]
    assert pos.pay_order(order["id"], "visa")["status"] == "paid"


# ---------------------------------------------------------------- payment

def test_card_payment_freezes_order_and_frees_table(pos):
    order = pos.open_order(table=1, pax=2)
    pos.add_item(order["id"], pos.croissant, 2)
    pos.add_item(order["id"], pos.latte)
    paid = pos.pay_order(order["id"], "visa")
    assert paid["totals"]["total_cents"] == 1439  # 1200 + 120 service + 119 GST
    assert paid["payment"]["change_cents"] == 0
    assert not pos.table_status()[0]["occupied"]
    with pytest.raises(ConflictError):
        pos.add_item(order["id"], pos.latte)
    with pytest.raises(ConflictError):
        pos.pay_order(order["id"], "visa")


def test_cash_change(pos):
    order = pos.open_order()
    pos.add_item(order["id"], pos.danish)  # 360 after 10% off, + 32 GST = 392
    paid = pos.pay_order(order["id"], "cash", tendered_cents=500)
    assert paid["totals"]["total_cents"] == 392
    assert paid["payment"]["change_cents"] == 108


def test_cash_problems(pos):
    order = pos.open_order()
    pos.add_item(order["id"], pos.danish)
    with pytest.raises(ValidationError):
        pos.pay_order(order["id"], "cash")
    with pytest.raises(ValidationError, match="less than"):
        pos.pay_order(order["id"], "cash", tendered_cents=100)
    assert pos.get_order(order["id"])["status"] == "open"


def test_cannot_pay_empty_order_or_unknown_method(pos):
    order = pos.open_order()
    with pytest.raises(ValidationError):
        pos.pay_order(order["id"], "visa")
    pos.add_item(order["id"], pos.latte)
    with pytest.raises(ValidationError):
        pos.pay_order(order["id"], "bitcoin")


def test_void_frees_table_and_keeps_record(pos):
    order = pos.open_order(table=2, pax=1)
    pos.add_item(order["id"], pos.latte)
    voided = pos.void_order(order["id"], "customer left")
    assert voided["status"] == "voided"
    assert not pos.table_status()[1]["occupied"]
    with pytest.raises(ConflictError):
        pos.void_order(order["id"])


# ---------------------------------------------------------------- reports

def make_sales(pos):
    dine = pos.open_order(table=1, pax=2)
    pos.add_item(dine["id"], pos.croissant, 2)
    pos.add_item(dine["id"], pos.latte)
    pos.pay_order(dine["id"], "visa")
    take = pos.open_order()
    pos.add_item(take["id"], pos.danish)
    pos.pay_order(take["id"], "cash", tendered_cents=500)


def test_sales_report(pos):
    make_sales(pos)
    voided = pos.open_order()
    pos.add_item(voided["id"], pos.latte, 9)
    pos.void_order(voided["id"])  # regression: the original counted voided items on the receipt

    r = pos.sales_report()
    assert (r["orders_paid"], r["orders_voided"]) == (2, 1)
    assert r["subtotal_cents"] == 1560 and r["discounts_cents"] == 40
    assert r["service_charge_cents"] == 120 and r["gst_cents"] == 151
    assert r["total_cents"] == 1831 and r["average_order_cents"] == 916
    assert r["by_payment_method"]["visa"]["amount_cents"] == 1439
    assert r["by_payment_method"]["cash"]["count"] == 1
    assert r["by_payment_method"]["master_card"]["count"] == 0
    assert [(i["name"], i["quantity"]) for i in r["items_sold"]] == [
        ("Croissant", 2), ("Cheese Danish", 1), ("Latte", 1)]


def test_close_day_locks_sales_and_starts_fresh(pos):
    make_sales(pos)
    z1 = pos.close_day()
    assert z1["number"] == 1 and z1["total_cents"] == 1831
    assert pos.sales_report()["total_cents"] == 0
    with pytest.raises(ConflictError, match="no sales"):
        pos.close_day()

    order = pos.open_order()
    pos.add_item(order["id"], pos.latte)
    pos.pay_order(order["id"], "cash", tendered_cents=1000)
    assert pos.close_day()["number"] == 2
    assert [z["number"] for z in pos.list_closed_reports()] == [2, 1]
    assert pos.list_closed_reports()[1]["total_cents"] == 1831  # history is not rewritten


def test_cannot_close_day_with_open_orders(pos):
    make_sales(pos)
    pos.open_order(table=4, pax=1)
    with pytest.raises(ConflictError, match="still open"):
        pos.close_day()


def test_changing_rates_does_not_change_past_sales(pos):
    make_sales(pos)
    pos.settings.gst_rate = pos.settings.gst_rate * 2
    assert pos.sales_report()["total_cents"] == 1831


# ---------------------------------------------------------------- receipt

def test_receipt_text(pos):
    order = pos.open_order()
    pos.add_item(order["id"], pos.danish, 2)
    bill = format_receipt_text(pos.get_receipt(order["id"]))
    assert "BILL #00001" in bill and "Takeaway" in bill and "Service charge" not in bill

    pos.pay_order(order["id"], "cash", tendered_cents=1000)
    text = format_receipt_text(pos.get_receipt(order["id"]))
    assert "RECEIPT #00001" in text and "2 x Cheese Danish" in text and "(10% off)" in text
    assert "Change" in text and "Thank you!" in text
    assert all(len(row) <= 40 for row in text.splitlines())


def test_long_names_do_not_break_receipt_width(pos):
    long_id = pos.add_menu_item("A" * 60, "Cakes", 123456)["id"]
    order = pos.open_order()
    pos.add_item(order["id"], long_id, 3)
    text = format_receipt_text(pos.get_receipt(order["id"]))
    assert all(len(row) <= 40 for row in text.splitlines())


# ---------------------------------------------------------------- persistence

def test_data_survives_restart(tmp_path):
    path = tmp_path / "pos.json"
    first = PosService(JsonStorage(path))
    item = first.add_menu_item("Bagel", "Bread", 300, discount_percent="12.5")["id"]
    order = first.open_order(table=2, pax=3)
    first.add_item(order["id"], item, 2)
    first.pay_order(order["id"], "cash", tendered_cents=2000)
    first.close_day()

    second = PosService(JsonStorage(path))  # simulates restarting the program
    assert second.list_menu()[0]["items"][0]["discount_percent"] == "12.5"
    assert second.get_order(order["id"])["payment"]["change_cents"] > 0
    assert second.list_closed_reports()[0]["number"] == 1
    assert second.open_order()["id"] == order["id"] + 1   # ids keep counting up
    assert second.add_menu_item("Pretzel", "Bread", 250)["id"] == item + 1


def test_corrupt_file_is_never_overwritten(tmp_path):
    path = tmp_path / "pos.json"
    path.write_text("{ this is not json", encoding="utf-8")
    with pytest.raises(StorageError):
        PosService(JsonStorage(path))
    assert path.read_text(encoding="utf-8") == "{ this is not json"
