"""Open an Order and resolve the master records it needs.

The desktop adapter is supplied later, after Fakturama's controls are inspected.
This module owns matching and creation decisions, not screen selectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol, Sequence

from .extract import validate
from .models import Address, Debtor, OrderData


class MasterDataReview(Exception):
    def __init__(self, step: str, reason: str) -> None:
        self.step = step
        self.reason = reason
        super().__init__(f"{step}: {reason}")


@dataclass(frozen=True)
class OpenOrderSpec:
    order_date: date
    external_reference: str
    price_mode: str = "Net"
    vat_mode: str = "With VAT"


@dataclass(frozen=True)
class DebtorSpec:
    company: str
    first_name: str
    last_name: str
    alias: str
    billing_address: Address
    delivery_address: Address
    email: str
    phone: str
    salutation: str = "---"
    discount_percent: Decimal = Decimal("0")
    price_mode: str = "Net"

    @property
    def single_address(self) -> bool:
        return self.billing_address == self.delivery_address


@dataclass(frozen=True)
class DebtorRecord:
    record_id: str
    company: str
    first_name: str
    last_name: str
    postal_code: str
    city: str


@dataclass(frozen=True)
class PaymentRecord:
    record_id: str
    name: str
    description: str
    code: str


@dataclass(frozen=True)
class PaymentSpec:
    name: str
    description: str
    code: str
    account: str = ""
    cash_discount: Decimal = Decimal("0")
    discount_days: int = 0
    net_days: int = 0
    unpaid_text: str = ""
    deposit_text: str = ""
    paid_text: str = ""
    standard: bool = False


@dataclass(frozen=True)
class VATRecord:
    record_id: str
    name: str
    value: Decimal
    code: str


@dataclass(frozen=True)
class VATSpec:
    name: str
    description: str
    value: Decimal
    code: str = "S (Standard rate)"


@dataclass(frozen=True)
class ProductRecord:
    record_id: str
    sku: str
    name: str
    description: str
    vat_name: str
    gross_price: Decimal


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name: str
    description: str
    vat_name: str
    gross_price: Decimal
    cost_price_net: Decimal = Decimal("0.00")
    stock: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class ResolvedMasters:
    order_number: str
    debtor_id: str
    payment_id: str
    product_ids: tuple[str, ...]


class MasterDataPort(Protocol):
    """Actions that a Fakturama desktop adapter must implement."""

    def open_order(self, spec: OpenOrderSpec) -> str: ...
    def order_is_open(self) -> bool: ...
    def find_debtors(self, company_or_name: str) -> Sequence[DebtorRecord]: ...
    def create_debtor(self, spec: DebtorSpec) -> None: ...
    def select_debtor(self, record_id: str) -> None: ...
    def debtor_addresses_match(self, debtor: Debtor) -> bool: ...
    def find_payments(self, name: str) -> Sequence[PaymentRecord]: ...
    def create_payment(self, spec: PaymentSpec) -> None: ...
    def find_vats(self, name: str) -> Sequence[VATRecord]: ...
    def create_vat(self, spec: VATSpec) -> None: ...
    def find_products(self, sku: str) -> Sequence[ProductRecord]: ...
    def create_product(self, spec: ProductSpec) -> None: ...
    def select_product(self, record_id: str) -> None: ...


PAYMENT_CODES = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def _same(left: str, right: str) -> bool:
    return " ".join(left.split()).casefold() == " ".join(right.split()).casefold()


def _contact_parts(name: str) -> tuple[str, str]:
    parts = name.strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def _vat_name(percentage: Decimal) -> str:
    return f"VAT {format(percentage.normalize(), 'f')}%"


def _ensure_open(ui: MasterDataPort, step: str) -> None:
    if not ui.order_is_open():
        raise MasterDataReview(step, "The Order editor is no longer open")


def _debtor(ui: MasterDataPort, debtor: Debtor) -> str:
    step = "debtor_resolution"
    first, last = _contact_parts(debtor.contact_name)
    query = debtor.company or debtor.contact_name
    rows = ui.find_debtors(query)
    candidates = [row for row in rows if _same(row.company, debtor.company) or (last and _same(row.last_name, last) and _same(row.first_name, first))]
    def is_exact(row: DebtorRecord) -> bool:
        return all((
            _same(row.company, debtor.company), _same(row.first_name, first),
            _same(row.last_name, last), _same(row.postal_code, debtor.billing_address.postal_code),
            _same(row.city, debtor.billing_address.city),
        ))

    exact = [row for row in candidates if is_exact(row)]
    if len(exact) > 1 or (exact and len(candidates) > 1):
        raise MasterDataReview(step, "Multiple debtor records match or conflict with the source")
    if not exact and candidates:
        raise MasterDataReview(step, "An existing debtor has the same identity but conflicting details")
    if not exact:
        ui.create_debtor(DebtorSpec(
            debtor.company, first, last, debtor.alias, debtor.billing_address,
            debtor.delivery_address, debtor.email, debtor.phone,
        ))
        _ensure_open(ui, step)
        rows = ui.find_debtors(query)
        exact = [row for row in rows if is_exact(row)]
        if len(exact) != 1:
            raise MasterDataReview(step, "Created debtor could not be uniquely found from the open Order")
    ui.select_debtor(exact[0].record_id)
    if not ui.debtor_addresses_match(debtor):
        raise MasterDataReview(step, "Selected debtor's invoice or delivery address does not match")
    return exact[0].record_id


def _payment(ui: MasterDataPort, method: str) -> str:
    step = "payment_resolution"
    code = PAYMENT_CODES.get(" ".join(method.split()).casefold())
    if code is None:
        raise MasterDataReview(step, f"Unsupported payment method: {method}")
    matches = [row for row in ui.find_payments(method) if _same(row.name, method)]
    if len(matches) > 1:
        raise MasterDataReview(step, "Multiple payment methods have the requested name")
    if matches and (not _same(matches[0].description, method) or not _same(matches[0].code, code)):
        raise MasterDataReview(step, "Existing payment method has conflicting settings")
    if not matches:
        ui.create_payment(PaymentSpec(method, method, code))
        _ensure_open(ui, step)
        matches = [row for row in ui.find_payments(method) if _same(row.name, method)]
        if len(matches) != 1 or not _same(matches[0].description, method) or not _same(matches[0].code, code):
            raise MasterDataReview(step, "Created payment method could not be verified")
    return matches[0].record_id


def _vat(ui: MasterDataPort, percentage: Decimal) -> str:
    step = "vat_resolution"
    name = _vat_name(percentage)
    matches = [row for row in ui.find_vats(name) if _same(row.name, name)]
    if len(matches) > 1:
        raise MasterDataReview(step, f"Multiple VAT records are named {name}")
    if matches and (matches[0].value != percentage or not _same(matches[0].code, "S (Standard rate)")):
        raise MasterDataReview(step, f"{name} has conflicting value or VAT code")
    if not matches:
        ui.create_vat(VATSpec(name, name, percentage))
        _ensure_open(ui, step)
        matches = [row for row in ui.find_vats(name) if _same(row.name, name)]
        if len(matches) != 1 or matches[0].value != percentage or not _same(matches[0].code, "S (Standard rate)"):
            raise MasterDataReview(step, f"Created {name} could not be verified")
    return name


def _product(ui: MasterDataPort, item, vat_name: str) -> str:
    step = "product_resolution"
    gross = (item.unit_net_price * (1 + item.vat_percent / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    spec = ProductSpec(item.sku, item.description, item.description, vat_name, gross)
    matches = [row for row in ui.find_products(item.sku) if row.sku == item.sku]
    if len(matches) > 1:
        raise MasterDataReview(step, f"Multiple products use SKU {item.sku}")
    if matches and not _product_matches(matches[0], spec):
        raise MasterDataReview(step, f"Product {item.sku} has conflicting description, VAT, or price")
    if not matches:
        ui.create_product(spec)
        _ensure_open(ui, step)
        matches = [row for row in ui.find_products(item.sku) if row.sku == item.sku]
        if len(matches) != 1 or not _product_matches(matches[0], spec):
            raise MasterDataReview(step, f"Created product {item.sku} could not be verified")
    ui.select_product(matches[0].record_id)
    return matches[0].record_id


def _product_matches(record: ProductRecord, spec: ProductSpec) -> bool:
    return (record.name == spec.name and record.description == spec.description
            and _same(record.vat_name, spec.vat_name) and record.gross_price == spec.gross_price)


def open_order_and_resolve(order: OrderData, ui: MasterDataPort) -> ResolvedMasters:
    """Leave the Order open with its debtor and products selected."""
    validate(order)
    if " ".join(order.payment.method.split()).casefold() not in PAYMENT_CODES:
        raise MasterDataReview("payment_resolution", f"Unsupported payment method: {order.payment.method}")
    number = ui.open_order(OpenOrderSpec(order.order_date, order.external_reference))
    if not number:
        raise MasterDataReview("order_open", "Fakturama did not show a generated Order number")
    _ensure_open(ui, "order_open")
    debtor_id = _debtor(ui, order.debtor)
    _ensure_open(ui, "debtor_resolution")
    payment_id = _payment(ui, order.payment.method)
    _ensure_open(ui, "payment_resolution")
    product_ids = []
    for item in order.items:
        vat_name = _vat(ui, item.vat_percent)
        _ensure_open(ui, "vat_resolution")
        product_ids.append(_product(ui, item, vat_name))
        _ensure_open(ui, "product_resolution")
    return ResolvedMasters(number, debtor_id, payment_id, tuple(product_ids))
