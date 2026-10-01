"""Complete the open Order, save it once, and verify it in Documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol, Sequence

from .master_data import ResolvedMasters
from .models import Item, OrderData


CENT = Decimal("0.01")


class OrderReview(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}: {reason}")


@dataclass(frozen=True)
class OrderLineView:
    sku: str
    quantity: Decimal
    unit_net_price: Decimal
    vat_percent: Decimal
    discount_percent: Decimal
    line_net_total: Decimal


@dataclass(frozen=True)
class OrderTotalsView:
    net: Decimal
    vat: Decimal
    gross: Decimal
    overall_discount_percent: Decimal
    shipping: Decimal


@dataclass(frozen=True)
class DocumentOrderRow:
    number: str
    order_date: date
    external_reference: str
    state: str
    gross_total: Decimal


class OrderCompletionPort(Protocol):
    def order_is_open(self) -> bool: ...
    def order_line_count(self) -> int: ...
    def fill_order_line(self, index: int, item: Item) -> None: ...
    def read_order_line(self, index: int) -> OrderLineView: ...
    def read_order_totals(self) -> OrderTotalsView: ...
    def save_order(self) -> None: ...
    def open_documents(self) -> None: ...
    def find_order_rows(self, number: str) -> Sequence[DocumentOrderRow]: ...


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _verify_line(index: int, expected: Item, actual: OrderLineView) -> None:
    if actual.sku != expected.sku:
        raise OrderReview("order_lines", f"Line {index + 1} has SKU {actual.sku!r}; expected {expected.sku!r}")
    if actual.quantity != expected.quantity:
        raise OrderReview("order_lines", f"Line {index + 1} quantity does not match the image")
    if _money(actual.unit_net_price) != _money(expected.unit_net_price):
        raise OrderReview("order_lines", f"Line {index + 1} net unit price does not match the image")
    if actual.vat_percent != expected.vat_percent or actual.discount_percent != expected.discount_percent:
        raise OrderReview("order_lines", f"Line {index + 1} VAT or discount does not match the image")
    if _money(actual.line_net_total) != _money(expected.source_line_total):
        raise OrderReview("order_lines", f"Line {index + 1} net total does not match the image")


def complete_and_verify_order(order: OrderData, resolved: ResolvedMasters, ui: OrderCompletionPort) -> DocumentOrderRow:
    """Use the selected product rows, then return the verified saved Order row."""
    if not ui.order_is_open():
        raise OrderReview("order_lines", "The Order editor is not open")
    if len(resolved.product_ids) != len(order.items) or ui.order_line_count() != len(order.items):
        raise OrderReview("order_lines", "Selected product line count does not match the image")

    for index, item in enumerate(order.items):
        ui.fill_order_line(index, item)
        _verify_line(index, item, ui.read_order_line(index))
    if ui.order_line_count() != len(order.items):
        raise OrderReview("order_lines", "The Order contains an unexpected extra or missing line")

    totals = ui.read_order_totals()
    if totals.overall_discount_percent != 0 or _money(totals.shipping) != 0:
        raise OrderReview("order_totals", "Unexpected overall discount or shipping charge")
    if (_money(totals.net), _money(totals.vat), _money(totals.gross)) != (
        _money(order.totals.net), _money(order.totals.vat), _money(order.totals.gross)
    ):
        raise OrderReview("order_totals", "Displayed net, VAT, or gross total does not match the image")
    if not ui.order_is_open():
        raise OrderReview("order_save", "The Order editor closed before saving")

    ui.save_order()  # Never retry an uncertain save: a duplicate Order could result.
    ui.open_documents()
    rows = [row for row in ui.find_order_rows(resolved.order_number) if row.number == resolved.order_number]
    if len(rows) != 1:
        raise OrderReview("order_verification", f"Expected one saved Order {resolved.order_number}, found {len(rows)}")
    row = rows[0]
    if row.order_date != order.order_date or row.external_reference != order.external_reference:
        raise OrderReview("order_verification", "Saved Order date or customer reference does not match")
    if row.state.strip().casefold() != "open":
        raise OrderReview("order_verification", f"Saved Order state is {row.state!r}, expected 'Open'")
    if _money(row.gross_total) != _money(order.totals.gross):
        raise OrderReview("order_verification", "Saved Order total does not match the image")
    return row
