"""Where the data is kept.

The service only talks to the tiny `load()` / `save()` interface below, so moving
to SQLite later means writing one new class here, not touching the business logic.
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Protocol

from .errors import StorageError
from .models import (MenuItem, Order, OrderLine, OrderStatus, Payment,
                     PaymentMethod, State, Totals)

FORMAT_VERSION = 1


# ---------- state <-> plain dict ----------

def state_to_dict(state: State) -> dict:
    return {
        "version": FORMAT_VERSION,
        "next_item_id": state.next_item_id,
        "next_order_id": state.next_order_id,
        "menu": [asdict(i) for i in state.menu.values()],
        "orders": [asdict(o) for o in state.orders.values()],
        "z_reports": state.z_reports,
    }


def _totals_from_dict(d: dict) -> Totals:
    return Totals(
        subtotal_cents=d["subtotal_cents"], discount_cents=d["discount_cents"],
        service_charge_cents=d["service_charge_cents"], gst_cents=d["gst_cents"],
        total_cents=d["total_cents"],
        service_charge_rate=Decimal(d["service_charge_rate"]), gst_rate=Decimal(d["gst_rate"]),
    )


def _order_from_dict(d: dict) -> Order:
    return Order(
        id=d["id"], table=d["table"], pax=d["pax"], created_at=d["created_at"],
        status=OrderStatus(d["status"]),
        lines=[OrderLine(**{**l, "discount_percent": Decimal(l["discount_percent"])}) for l in d["lines"]],
        next_line_id=d["next_line_id"], paid_at=d["paid_at"],
        payment=Payment(**{**d["payment"], "method": PaymentMethod(d["payment"]["method"])}) if d["payment"] else None,
        totals=_totals_from_dict(d["totals"]) if d["totals"] else None,
        void_reason=d["void_reason"], closed_in=d["closed_in"],
    )


def state_from_dict(data: dict) -> State:
    if data.get("version") != FORMAT_VERSION:
        raise StorageError(f"Unsupported data file version: {data.get('version')!r}")
    menu = [MenuItem(**{**i, "discount_percent": Decimal(i["discount_percent"])}) for i in data["menu"]]
    orders = [_order_from_dict(o) for o in data["orders"]]
    return State(
        menu={i.id: i for i in menu},
        orders={o.id: o for o in orders},
        z_reports=data["z_reports"],
        next_item_id=data["next_item_id"],
        next_order_id=data["next_order_id"],
    )


def _encode(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"Cannot save {type(obj).__name__}")


# ---------- storage backends ----------

class Storage(Protocol):
    """The two methods the service needs. Any class with these works (JSON now, SQLite later)."""

    def load(self) -> State | None: ...

    def save(self, state: State) -> None: ...


class MemoryStorage:
    """Keeps data in memory only. Used by tests."""

    def __init__(self) -> None:
        self._state: State | None = None

    def load(self) -> State | None:
        return copy.deepcopy(self._state)

    def save(self, state: State) -> None:
        self._state = copy.deepcopy(state)


class JsonStorage:
    """Keeps everything in one JSON file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> State | None:
        if not self.path.exists():
            return None
        try:
            return state_from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except StorageError:
            raise
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Refuse to start with an empty shop and silently overwrite the real data.
            raise StorageError(f"Could not read {self.path}: {exc}. The file was left untouched.") from exc

    def save(self, state: State) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(state_to_dict(state), indent=2, default=_encode), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic: a crash mid-save can't leave a half-written file
        except OSError as exc:
            raise StorageError(f"Could not save to {self.path}: {exc}") from exc
