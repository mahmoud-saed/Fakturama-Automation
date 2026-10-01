import unittest
from datetime import date
from decimal import Decimal

from fakturama_automation.extract import ReviewRequired, _date, _decimal, extract, validate
from fakturama_automation.models import Address, Debtor, Item, OrderData, Payment, Totals
from fakturama_automation.ocr import Page, Word


def sample_order() -> OrderData:
    billing = Address("Northstar Office GmbH", "Friedrichstrasse 88", "10117", "Berlin", "Germany")
    delivery = Address("Northstar Office Warehouse", "Industriestrasse 44", "10553", "Berlin", "Germany")
    return OrderData(
        date(2026, 7, 14), "WEB-2026-0714-A17",
        Debtor("Northstar Office GmbH", "Marta Klein", "NORTHSTAR-BERLIN", "marta.klein@example.test", "+49 30 5550 1420", billing, delivery),
        Payment("Bank Transfer", "PAID", date(2026, 7, 18)),
        (
            Item("CHR-ERG-01", "Ergonomic Desk Chair", Decimal("2"), Decimal("250.00"), Decimal("19"), Decimal("10"), Decimal("450.00")),
            Item("MAT-DESK-02", "Anti-Fatigue Desk Mat", Decimal("3"), Decimal("40.00"), Decimal("19"), Decimal("0"), Decimal("120.00")),
        ),
        Totals(Decimal("570.00"), Decimal("108.30"), Decimal("678.30")),
    )


def sample_page(low_confidence=False) -> Page:
    words = []

    def put(x, y, text):
        for value in text.split():
            confidence = 40 if low_confidence and value == "450.00" else 95
            words.append(Word(value, confidence, x, y, len(value) * 9, 14))
            x += len(value) * 9 + 10

    for x, y, text in [
        (60, 100, "ORDER"), (60, 120, "EXTERNAL REFERENCE"), (290, 120, "ORDER DATE"),
        (60, 150, "WEB-2026-0714-A17"), (290, 150, "2026-07-14"),
        (60, 250, "CUSTOMER AND CONTACT"),
        (60, 280, "COMPANY"), (520, 280, "CONTACT NAME"),
        (60, 310, "Northstar Office GmbH"), (520, 310, "Marta Klein"),
        (60, 340, "CUSTOMER ALIAS"), (520, 340, "EMAIL"),
        (60, 370, "NORTHSTAR-BERLIN"), (520, 370, "marta.klein@example.test"),
        (520, 390, "PHONE"), (520, 410, "+49 30 5550 1420"),
        (60, 430, "ADDRESSES"), (60, 460, "BILLING ADDRESS"), (520, 460, "DELIVERY ADDRESS"),
        (60, 490, "Northstar Office GmbH"), (520, 490, "Northstar Office Warehouse"),
        (60, 520, "Friedrichstrasse 88"), (520, 520, "Industriestrasse 44"),
        (60, 550, "10117 Berlin"), (520, 550, "10553 Berlin"),
        (60, 580, "Germany"), (520, 580, "Germany"),
        (60, 700, "PAYMENT"), (60, 730, "PAYMENT METHOD"), (370, 730, "PAID STATUS"), (675, 730, "PAYMENT DATE"),
        (60, 760, "Bank Transfer"), (370, 760, "PAID"), (675, 760, "2026-07-18"),
        (60, 850, "ITEMS"),
        (150, 900, "CHR-ERG-01"), (260, 900, "Ergonomic Desk Chair"), (530, 900, "2"),
        (610, 900, "250.00"), (735, 900, "10%"), (790, 900, "19%"), (875, 900, "450.00"),
        (150, 940, "MAT-DESK-02"), (260, 940, "Anti-Fatigue Desk Mat"), (530, 940, "3"),
        (610, 940, "40.00"), (735, 940, "0%"), (790, 940, "19%"), (875, 940, "120.00"),
        (60, 1200, "NET TOTAL"), (370, 1200, "VAT TOTAL"), (675, 1200, "GROSS TOTAL"),
        (60, 1230, "EUR 570.00"), (370, 1230, "EUR 108.30"), (675, 1230, "EUR 678.30"),
    ]:
        put(x, y, text)
    return Page(1000, 1400, tuple(words))


class ExtractionValidationTests(unittest.TestCase):
    def test_extracts_sample_layout(self):
        order = extract(sample_page())
        self.assertEqual(order, sample_order())

    def test_low_confidence_value_stops(self):
        with self.assertRaisesRegex(ReviewRequired, "Uncertain OCR"):
            extract(sample_page(low_confidence=True))

    def test_missing_source_totals_stops(self):
        page = sample_page()
        page = Page(page.width, page.height, tuple(word for word in page.words if word.y != 1230))
        with self.assertRaisesRegex(ReviewRequired, "net total"):
            extract(page)

    def test_paid_source_without_payment_date_stops(self):
        page = sample_page()
        page = Page(page.width, page.height, tuple(word for word in page.words if not (word.y == 760 and word.x >= 675)))
        with self.assertRaisesRegex(ReviewRequired, "payment date"):
            extract(page)

    def test_sample_totals_and_serialization(self):
        order = sample_order()
        validate(order)
        self.assertEqual(order.to_dict()["totals"]["gross"], "678.30")
        self.assertEqual(order.to_dict()["order_date"], "2026-07-14")

    def test_paid_needs_date(self):
        order = sample_order()
        order = OrderData(order.order_date, order.external_reference, order.debtor, Payment("Bank Transfer", "PAID", None), order.items, order.totals)
        with self.assertRaisesRegex(ReviewRequired, "payment date"):
            validate(order)

    def test_line_total_mismatch_stops(self):
        order = sample_order()
        bad = Item("CHR-ERG-01", "Ergonomic Desk Chair", Decimal("2"), Decimal("250"), Decimal("19"), Decimal("10"), Decimal("451"))
        order = OrderData(order.order_date, order.external_reference, order.debtor, order.payment, (bad, *order.items[1:]), order.totals)
        with self.assertRaisesRegex(ReviewRequired, "line total"):
            validate(order)

    def test_decimal_and_date_normalization(self):
        self.assertEqual(_decimal("EUR 1,234.50", "amount"), Decimal("1234.50"))
        self.assertEqual(_date("2026-07-14", "date"), date(2026, 7, 14))
        with self.assertRaises(ReviewRequired):
            _date("2026-02-30", "date")


if __name__ == "__main__":
    unittest.main()
