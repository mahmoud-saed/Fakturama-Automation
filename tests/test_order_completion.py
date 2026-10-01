import unittest
from dataclasses import replace
from decimal import Decimal

from fakturama_automation.master_data import ResolvedMasters
from fakturama_automation.order_completion import (
    DocumentOrderRow, OrderLineView, OrderReview, OrderTotalsView,
    complete_and_verify_order,
)
from tests.test_extraction import sample_order


class FakeOrderDesktop:
    def __init__(self):
        order = sample_order()
        self.open = True
        self.lines = [None] * len(order.items)
        self.filled = []
        self.save_count = 0
        self.documents_opened = False
        self.line_override = None
        self.totals = OrderTotalsView(Decimal("570"), Decimal("108.30"), Decimal("678.30"), Decimal("0"), Decimal("0"))
        self.rows = [DocumentOrderRow("ORD-100", order.order_date, order.external_reference, "Open", Decimal("678.30"))]

    def order_is_open(self):
        return self.open

    def order_line_count(self):
        return len(self.lines)

    def fill_order_line(self, index, item):
        self.filled.append(index)
        self.lines[index] = OrderLineView(item.sku, item.quantity, item.unit_net_price, item.vat_percent, item.discount_percent, item.source_line_total)

    def read_order_line(self, index):
        return self.line_override or self.lines[index]

    def read_order_totals(self):
        return self.totals

    def save_order(self):
        self.save_count += 1

    def open_documents(self):
        self.documents_opened = True

    def find_order_rows(self, number):
        return self.rows


class OrderCompletionTests(unittest.TestCase):
    def setUp(self):
        self.order = sample_order()
        self.resolved = ResolvedMasters("ORD-100", "D1", "PAY1", ("P1", "P2"))
        self.ui = FakeOrderDesktop()

    def test_fills_source_order_and_verifies_saved_row(self):
        row = complete_and_verify_order(self.order, self.resolved, self.ui)
        self.assertEqual(self.ui.filled, [0, 1])
        self.assertEqual(self.ui.save_count, 1)
        self.assertTrue(self.ui.documents_opened)
        self.assertEqual(row.number, "ORD-100")

    def test_wrong_line_value_stops_before_save(self):
        item = self.order.items[0]
        self.ui.line_override = OrderLineView(item.sku, item.quantity, item.unit_net_price, item.vat_percent, Decimal("0"), item.source_line_total)
        with self.assertRaisesRegex(OrderReview, "VAT or discount"):
            complete_and_verify_order(self.order, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 0)

    def test_total_mismatch_stops_before_save(self):
        self.ui.totals = replace(self.ui.totals, gross=Decimal("678.31"))
        with self.assertRaisesRegex(OrderReview, "Displayed net, VAT, or gross"):
            complete_and_verify_order(self.order, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 0)

    def test_duplicate_saved_order_is_manual_review_without_resave(self):
        self.ui.rows.append(self.ui.rows[0])
        with self.assertRaisesRegex(OrderReview, "found 2"):
            complete_and_verify_order(self.order, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 1)

    def test_wrong_saved_state_is_manual_review(self):
        self.ui.rows = [replace(self.ui.rows[0], state="Closed")]
        with self.assertRaisesRegex(OrderReview, "expected 'Open'"):
            complete_and_verify_order(self.order, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 1)


if __name__ == "__main__":
    unittest.main()
