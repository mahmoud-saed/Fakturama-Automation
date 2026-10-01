"""Create an Invoice from a saved Order and verify both documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, Sequence

from .extract import validate
from .master_data import ResolvedMasters
from .models import Address, OrderData
from .order_completion import DocumentOrderRow, OrderLineView, OrderReview, OrderTotalsView, _money, _verify_line


class InvoiceReview(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}: {reason}")


@dataclass(frozen=True)
class InvoiceView:
    number: str
    invoice_date: date
    service_date: date
    linked_order_number: str
    external_reference: str
    order_date: date
    invoice_address: Address
    delivery_address: Address
    vat_mode: str
    lines: tuple[OrderLineView, ...]
    totals: OrderTotalsView
    payment_method: str
    paid: bool
    payment_date: date | None
    payment_value: Decimal | None


@dataclass(frozen=True)
class InvoiceDocumentRow:
    number: str
    invoice_date: date
    service_date: date
    linked_order_number: str
    external_reference: str
    gross_total: Decimal
    payment_method: str
    paid: bool
    payment_date: date | None
    payment_value: Decimal | None


class InvoicePort(Protocol):
    def create_follow_up_invoice(self, order_number: str) -> None: ...
    def read_invoice(self) -> InvoiceView: ...
    def set_invoice_payment(self, payment_id: str, paid: bool, payment_date: date | None, value: Decimal | None) -> None: ...
    def save_invoice(self) -> None: ...
    def open_documents(self) -> None: ...
    def find_invoice_rows(self, number: str) -> Sequence[InvoiceDocumentRow]: ...
    def find_order_rows(self, number: str) -> Sequence[DocumentOrderRow]: ...


def _check_copied(order: OrderData, saved: DocumentOrderRow, invoice: InvoiceView) -> None:
    if not invoice.number or not isinstance(invoice.invoice_date, date) or not isinstance(invoice.service_date, date):
        raise InvoiceReview("invoice_copy", "Generated Invoice number, date, or service date is missing")
    if invoice.linked_order_number != saved.number:
        raise InvoiceReview("invoice_copy", "Invoice is not linked to the saved Order")
    if invoice.external_reference != order.external_reference or invoice.order_date != order.order_date:
        raise InvoiceReview("invoice_copy", "Customer reference or Order date was not copied")
    if invoice.invoice_address != order.debtor.billing_address or invoice.delivery_address != order.debtor.delivery_address:
        raise InvoiceReview("invoice_copy", "Invoice or delivery address was not copied")
    if invoice.vat_mode.strip().casefold() != "with vat":
        raise InvoiceReview("invoice_copy", "VAT mode was not copied")
    if len(invoice.lines) != len(order.items):
        raise InvoiceReview("invoice_copy", "Invoice item count does not match the Order")
    for index, (expected, actual) in enumerate(zip(order.items, invoice.lines)):
        try:
            _verify_line(index, expected, actual)
        except OrderReview as exc:
            raise InvoiceReview("invoice_copy", exc.reason) from exc
    totals = invoice.totals
    if totals.overall_discount_percent != 0 or _money(totals.shipping) != 0:
        raise InvoiceReview("invoice_copy", "Invoice has an unexpected overall discount or shipping charge")
    if (_money(totals.net), _money(totals.vat), _money(totals.gross)) != (
        _money(order.totals.net), _money(order.totals.vat), _money(order.totals.gross)
    ):
        raise InvoiceReview("invoice_copy", "Invoice totals do not match the saved Order")


def create_and_verify_invoice(
    order: OrderData, saved: DocumentOrderRow, resolved: ResolvedMasters, ui: InvoicePort,
) -> InvoiceDocumentRow:
    """Use the Order's follow-up action; never create an unrelated Invoice."""
    validate(order)
    if order.payment.status not in {"PAID", "UNPAID"}:
        raise InvoiceReview("invoice_start", "Payment status must be PAID or UNPAID")
    if saved.number != resolved.order_number or saved.state.strip().casefold() != "open":
        raise InvoiceReview("invoice_start", "The verified saved Order is missing or not open")
    if saved.order_date != order.order_date or saved.external_reference != order.external_reference or _money(saved.gross_total) != _money(order.totals.gross):
        raise InvoiceReview("invoice_start", "Saved Order no longer matches the source image")
    if not resolved.payment_id:
        raise InvoiceReview("invoice_start", "No resolved payment method is available")
    if order.payment.status == "PAID" and order.payment.payment_date is None:
        raise InvoiceReview("invoice_start", "PAID source is missing a payment date")

    ui.create_follow_up_invoice(saved.number)
    initial = ui.read_invoice()
    _check_copied(order, saved, initial)

    paid = order.payment.status == "PAID"
    payment_date = order.payment.payment_date if paid else None
    payment_value = _money(order.totals.gross) if paid else None
    ui.set_invoice_payment(resolved.payment_id, paid, payment_date, payment_value)
    final = ui.read_invoice()
    _check_copied(order, saved, final)
    if (final.number, final.invoice_date, final.service_date) != (initial.number, initial.invoice_date, initial.service_date):
        raise InvoiceReview("invoice_payment", "Generated Invoice number or dates changed")
    if final.payment_method != order.payment.method or final.paid != paid:
        raise InvoiceReview("invoice_payment", "Payment method or paid state does not match the image")
    if final.payment_date != payment_date or (None if final.payment_value is None else _money(final.payment_value)) != payment_value:
        raise InvoiceReview("invoice_payment", "Payment date or value does not match the image")

    ui.save_invoice()  # Do not retry an uncertain save.
    ui.open_documents()
    invoice_rows = [row for row in ui.find_invoice_rows(initial.number) if row.number == initial.number]
    order_rows = [row for row in ui.find_order_rows(saved.number) if row.number == saved.number]
    if len(invoice_rows) != 1 or len(order_rows) != 1:
        raise InvoiceReview("invoice_verification", "Expected one saved Invoice and one original Order")
    invoice_row, order_row = invoice_rows[0], order_rows[0]
    if (invoice_row.invoice_date, invoice_row.service_date) != (initial.invoice_date, initial.service_date):
        raise InvoiceReview("invoice_verification", "Saved Invoice dates changed")
    if invoice_row.linked_order_number != saved.number or invoice_row.external_reference != order.external_reference:
        raise InvoiceReview("invoice_verification", "Saved Invoice lost its Order link or customer reference")
    if _money(invoice_row.gross_total) != _money(order.totals.gross):
        raise InvoiceReview("invoice_verification", "Saved Invoice total does not match the image")
    if invoice_row.payment_method != order.payment.method or invoice_row.paid != paid:
        raise InvoiceReview("invoice_verification", "Saved Invoice payment method or state does not match")
    if invoice_row.payment_date != payment_date or (None if invoice_row.payment_value is None else _money(invoice_row.payment_value)) != payment_value:
        raise InvoiceReview("invoice_verification", "Saved Invoice payment date or value does not match")
    if (order_row.order_date != saved.order_date or order_row.external_reference != saved.external_reference
            or order_row.state.strip().casefold() != "open" or _money(order_row.gross_total) != _money(saved.gross_total)):
        raise InvoiceReview("invoice_verification", "Original Order is missing, changed, or no longer open")
    return invoice_row
