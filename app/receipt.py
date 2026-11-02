"""Turns a receipt dict (from PosService.get_receipt) into printable plain text.

Plain text works for a browser print preview today and a thermal printer later.
"""
from __future__ import annotations

from .money import format_money


def _row(left: str, right: str, width: int) -> str:
    if len(left) + len(right) + 1 > width:
        left = left[: max(1, width - len(right) - 4)] + "..."
    return left + " " * (width - len(left) - len(right)) + right


def format_receipt_text(receipt: dict, width: int = 40) -> str:
    money = lambda cents: format_money(cents, receipt["currency_symbol"])
    t = receipt["totals"]
    lines: list[str] = []

    lines += [receipt["business_name"].center(width),
              f"{receipt['title']} #{receipt['receipt_number']}".center(width),
              (receipt["paid_at"] or receipt["created_at"]).replace("T", " ").center(width)]
    if receipt["table"] is None:
        lines.append("Takeaway".center(width))
    else:
        lines.append(f"Table {receipt['table']} - {receipt['pax']} guest(s)".center(width))
    lines.append("-" * width)

    for l in receipt["lines"]:
        lines.append(_row(f"{l['quantity']} x {l['name']}", money(l["line_total_cents"]), width))
        if l["discount_percent"] != "0":
            lines.append(f"    ({l['discount_percent']}% off)")
    lines.append("-" * width)

    lines.append(_row("Subtotal", money(t["subtotal_cents"]), width))
    if t["service_charge_percent"] != "0":
        lines.append(_row(f"Service charge ({t['service_charge_percent']}%)", money(t["service_charge_cents"]), width))
    if t["gst_percent"] != "0":
        lines.append(_row(f"GST ({t['gst_percent']}%)", money(t["gst_cents"]), width))
    lines.append(_row("TOTAL", money(t["total_cents"]), width))
    if t["discount_cents"]:
        lines.append(_row("You saved", money(t["discount_cents"]), width))

    p = receipt["payment"]
    if p:
        lines.append("-" * width)
        lines.append(_row("Paid by", p["label"], width))
        if p["method"] == "cash":
            lines.append(_row("Cash received", money(p["tendered_cents"]), width))
            lines.append(_row("Change", money(p["change_cents"]), width))
    if receipt["status"] == "voided":
        lines += ["", "*** VOIDED ***".center(width)]
    elif receipt["status"] == "paid":
        lines += ["", "Thank you!".center(width)]
    return "\n".join(lines)
