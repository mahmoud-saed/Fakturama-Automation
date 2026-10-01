from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Address:
    company: str
    street: str
    postal_code: str
    city: str
    country: str


@dataclass(frozen=True)
class Debtor:
    company: str
    contact_name: str
    alias: str
    email: str
    phone: str
    billing_address: Address
    delivery_address: Address


@dataclass(frozen=True)
class Payment:
    method: str
    status: str
    payment_date: date | None


@dataclass(frozen=True)
class Item:
    sku: str
    description: str
    quantity: Decimal
    unit_net_price: Decimal
    vat_percent: Decimal
    discount_percent: Decimal
    source_line_total: Decimal


@dataclass(frozen=True)
class Totals:
    net: Decimal
    vat: Decimal
    gross: Decimal


@dataclass(frozen=True)
class OrderData:
    order_date: date
    external_reference: str
    debtor: Debtor
    payment: Payment
    items: tuple[Item, ...]
    totals: Totals

    def to_dict(self) -> dict:
        def convert(value):
            if isinstance(value, Decimal):
                return str(value)
            if isinstance(value, date):
                return value.isoformat()
            if isinstance(value, dict):
                return {key: convert(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [convert(item) for item in value]
            return value

        return convert(asdict(self))
