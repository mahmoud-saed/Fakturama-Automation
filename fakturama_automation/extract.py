from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .models import Address, Debtor, Item, OrderData, Payment, Totals
from .ocr import Page, Word


class ReviewRequired(Exception):
    """The source image cannot be used safely without a human review."""


MONEY = Decimal("0.01")
MIN_CONFIDENCE = 70.0
SKU = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$", re.I)
NUMBER = re.compile(r"-?\d[\d.,]*")


def _lines(words: tuple[Word, ...] | list[Word]) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda item: (item.center_y, item.x)):
        for line in lines:
            if abs(word.center_y - sum(w.center_y for w in line) / len(line)) <= max(word.height, max(w.height for w in line)) * .45:
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda word: word.x) for line in sorted(lines, key=lambda line: min(word.y for word in line))]


def _text(words: list[Word]) -> str:
    return " ".join(word.text for word in words).strip()


def _trusted(words: list[Word], field: str) -> str:
    if not words:
        raise ReviewRequired(f"Missing {field}")
    uncertain = [word.text for word in words if word.confidence < MIN_CONFIDENCE]
    if uncertain:
        raise ReviewRequired(f"Uncertain OCR for {field}: {', '.join(uncertain)}")
    return _text(words)


def _date(value: str, field: str) -> date:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", value)
    if not match:
        raise ReviewRequired(f"Missing or invalid {field}")
    try:
        return date.fromisoformat(match.group())
    except ValueError as exc:
        raise ReviewRequired(f"Invalid {field}") from exc


def _decimal(value: str, field: str) -> Decimal:
    match = NUMBER.search(value.replace(" ", ""))
    if not match:
        raise ReviewRequired(f"Missing {field}")
    raw = match.group()
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ReviewRequired(f"Invalid {field}") from exc


def _headings(page: Page) -> dict[str, float]:
    aliases = {
        "order": "ORDER", "customer": "CUSTOMER AND CONTACT", "addresses": "ADDRESSES",
        "payment": "PAYMENT", "items": "ITEMS", "totals": "NET TOTAL",
    }
    found = {}
    for line in _lines(page.words):
        label = _text(line).upper()
        for key, phrase in aliases.items():
            if label.startswith(phrase):
                found.setdefault(key, min(word.y for word in line))
    if len(found) != len(aliases):
        raise ReviewRequired("Order image sections are missing or OCR could not identify them")
    expected = [found[key] for key in aliases]
    if expected != sorted(expected):
        raise ReviewRequired("Order image sections are out of order")
    return found


def _region(page: Page, top: float, bottom: float, left: float, right: float) -> list[list[Word]]:
    return _lines([word for word in page.words if top < word.center_y < bottom and left <= word.x + word.width / 2 < right])


def _value_after(lines: list[list[Word]], label: str, field: str, required: bool = True) -> str:
    for index, line in enumerate(lines):
        if label in _text(line).upper():
            if index + 1 < len(lines):
                return _trusted(lines[index + 1], field)
            break
    if required:
        raise ReviewRequired(f"Missing {field}")
    return ""


def _address(lines: list[list[Word]], label: str) -> Address:
    start = next((index for index, line in enumerate(lines) if label in _text(line).upper()), None)
    if start is None:
        raise ReviewRequired(f"Missing {label.lower()}")
    values = lines[start + 1:]
    if len(values) < 4:
        raise ReviewRequired(f"Incomplete {label.lower()}")
    company = _trusted(values[0], f"{label.lower()} company")
    street = _trusted(values[1], f"{label.lower()} street")
    postal_city = _trusted(values[2], f"{label.lower()} ZIP and city")
    match = re.fullmatch(r"(\S+)\s+(.+)", postal_city)
    if not match:
        raise ReviewRequired(f"Invalid {label.lower()} ZIP and city")
    country = _trusted(values[3], f"{label.lower()} country")
    return Address(company, street, match.group(1), match.group(2), country)


def _item(words: list[Word], x0: float, width: float, index: int) -> Item:
    # Fractions describe the source document's printed table, not screen positions.
    edges = [0, .09, .20, .48, .54, .60, .72, .79, .86, 1]
    columns = [[] for _ in range(9)]
    for word in words:
        position = (word.x + word.width / 2 - x0) / width
        for column in range(9):
            if edges[column] <= position < edges[column + 1]:
                columns[column].append(word)
                break
    # Tesseract can read the SKU cell's table border as isolated punctuation.
    columns[1] = [word for word in columns[1] if word.text not in {"=", "|"}]
    fields = ["SKU", "description", "quantity", "unit price", "discount", "VAT", "line total"]
    values = [columns[1], columns[2], columns[3], columns[5], columns[6], columns[7], columns[8]]
    parsed = [_trusted(value, f"item {index} {field}") for field, value in zip(fields, values)]
    if not SKU.fullmatch(parsed[0]):
        raise ReviewRequired(f"Invalid item {index} SKU")
    return Item(
        parsed[0], parsed[1], _decimal(parsed[2], f"item {index} quantity"),
        _decimal(parsed[3], f"item {index} unit price"),
        _decimal(parsed[5], f"item {index} VAT"),
        _decimal(parsed[4], f"item {index} discount"),
        _decimal(parsed[6], f"item {index} line total"),
    )


def extract(page: Page) -> OrderData:
    h = _headings(page)
    left = page.width * .055
    right = page.width * .975
    span = right - left

    order_cells = [_region(page, h["order"] + 8, h["customer"], left + span * i / 4, left + span * (i + 1) / 4) for i in range(4)]
    reference = _value_after(order_cells[0], "EXTERNAL REFERENCE", "external reference")
    order_date = _date(_value_after(order_cells[1], "ORDER DATE", "order date"), "order date")

    customer_cells = [_region(page, h["customer"] + 8, h["addresses"], left + span * i / 2, left + span * (i + 1) / 2) for i in range(2)]
    company = _value_after(customer_cells[0], "COMPANY", "company")
    alias = _value_after(customer_cells[0], "CUSTOMER ALIAS", "customer alias", False)
    contact = _value_after(customer_cells[1], "CONTACT NAME", "contact name", False)
    email = _value_after(customer_cells[1], "EMAIL", "email", False)
    phone = _value_after(customer_cells[1], "PHONE", "phone", False)

    address_cells = [_region(page, h["addresses"] + 8, h["payment"], left + span * i / 2, left + span * (i + 1) / 2) for i in range(2)]
    billing = _address(address_cells[0], "BILLING ADDRESS")
    delivery = _address(address_cells[1], "DELIVERY ADDRESS")
    debtor = Debtor(company, contact, alias, email, phone, billing, delivery)

    payment_cells = [_region(page, h["payment"] + 8, h["items"], left + span * i / 3, left + span * (i + 1) / 3) for i in range(3)]
    method = _value_after(payment_cells[0], "PAYMENT METHOD", "payment method")
    status = _value_after(payment_cells[1], "PAID STATUS", "paid status").upper()
    if status not in {"PAID", "UNPAID"}:
        raise ReviewRequired("Paid status must be PAID or UNPAID")
    date_text = _value_after(payment_cells[2], "PAYMENT DATE", "payment date", False)
    payment_date = _date(date_text, "payment date") if date_text else None
    if status == "PAID" and payment_date is None:
        raise ReviewRequired("PAID order is missing a payment date")
    payment = Payment(method, status, payment_date)

    item_lines = _region(page, h["items"] + 8, h["totals"], left, right)
    sku_rows = [line for line in item_lines if any(SKU.fullmatch(word.text) for word in line)]
    if not sku_rows:
        raise ReviewRequired("No order items found")
    items = tuple(_item(row, left, span, index) for index, row in enumerate(sku_rows, 1))

    total_cells = [_region(page, h["totals"] - 5, page.height, left + span * i / 3, left + span * (i + 1) / 3) for i in range(3)]
    totals = Totals(*(
        _decimal(_value_after(cell, label, label.lower()), label.lower())
        for cell, label in zip(total_cells, ("NET TOTAL", "VAT TOTAL", "GROSS TOTAL"))
    ))
    order = OrderData(order_date, reference, debtor, payment, items, totals)
    validate(order)
    return order


def validate(order: OrderData) -> None:
    if not order.external_reference or not order.debtor.company or not order.payment.method:
        raise ReviewRequired("Required order, debtor, or payment data is missing")
    if order.payment.status == "PAID" and order.payment.payment_date is None:
        raise ReviewRequired("PAID order is missing a payment date")
    if not order.items:
        raise ReviewRequired("No order items found")
    net = Decimal("0")
    vat = Decimal("0")
    for index, item in enumerate(order.items, 1):
        if item.quantity <= 0 or item.unit_net_price < 0 or not 0 <= item.discount_percent <= 100 or not 0 <= item.vat_percent <= 100:
            raise ReviewRequired(f"Invalid quantity, price, discount, or VAT for item {index}")
        line_net = (item.quantity * item.unit_net_price * (1 - item.discount_percent / 100)).quantize(MONEY, rounding=ROUND_HALF_UP)
        if line_net != item.source_line_total:
            raise ReviewRequired(f"Item {index} line total does not match its quantity, price, and discount")
        net += line_net
        vat += (line_net * item.vat_percent / 100).quantize(MONEY, rounding=ROUND_HALF_UP)
    if (net, vat, net + vat) != (order.totals.net, order.totals.vat, order.totals.gross):
        raise ReviewRequired("Source totals do not match the order items")
