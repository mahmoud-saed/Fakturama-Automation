import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from fakturama_automation.invoice import InvoiceDocumentRow, InvoiceReview, InvoiceView, create_and_verify_invoice
from fakturama_automation.master_data import ResolvedMasters
from fakturama_automation.order_completion import DocumentOrderRow, OrderLineView, OrderTotalsView
from tests.test_extraction import sample_order


class FakeInvoiceDesktop:
    def __init__(self, order):
        self.actions = []
        self.invoice = InvoiceView(
            "INV-200", date(2026, 7, 19), date(2026, 7, 19), "ORD-100",
            order.external_reference, order.order_date, order.debtor.billing_address,
            order.debtor.delivery_address, "With VAT",
            tuple(OrderLineView(item.sku, item.quantity, item.unit_net_price, item.vat_percent, item.discount_percent, item.source_line_total) for item in order.items),
            OrderTotalsView(order.totals.net, order.totals.vat, order.totals.gross, Decimal("0"), Decimal("0")),
            "", False, None, None,
        )
        self.invoice_rows = []
        self.order_rows = [DocumentOrderRow("ORD-100", order.order_date, order.external_reference, "Open", order.totals.gross)]
        self.payment_args = None
        self.save_count = 0

    def create_follow_up_invoice(self, order_number):
        self.actions.append(("follow_up_invoice", order_number))

    def read_invoice(self):
        return self.invoice

    def set_invoice_payment(self, payment_id, paid, payment_date, value):
        self.payment_args = (payment_id, paid, payment_date, value)
        self.invoice = replace(self.invoice, payment_method="Bank Transfer", paid=paid, payment_date=payment_date, payment_value=value)

    def save_invoice(self):
        self.save_count += 1
        self.invoice_rows = [InvoiceDocumentRow(
            self.invoice.number, self.invoice.invoice_date, self.invoice.service_date,
            self.invoice.linked_order_number, self.invoice.external_reference,
            self.invoice.totals.gross, self.invoice.payment_method, self.invoice.paid,
            self.invoice.payment_date, self.invoice.payment_value,
        )]

    def open_documents(self):
        self.actions.append(("open_documents",))

    def find_invoice_rows(self, number):
        return self.invoice_rows

    def find_order_rows(self, number):
        return self.order_rows


class InvoiceTests(unittest.TestCase):
    def setUp(self):
        self.order = sample_order()
        self.saved = DocumentOrderRow("ORD-100", self.order.order_date, self.order.external_reference, "Open", self.order.totals.gross)
        self.resolved = ResolvedMasters("ORD-100", "D1", "PAY1", ("P1", "P2"))
        self.ui = FakeInvoiceDesktop(self.order)

    def test_paid_invoice_uses_follow_up_and_verifies_both_documents(self):
        row = create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.actions[0], ("follow_up_invoice", "ORD-100"))
        self.assertEqual(self.ui.payment_args, ("PAY1", True, date(2026, 7, 18), Decimal("678.30")))
        self.assertEqual(self.ui.save_count, 1)
        self.assertEqual(row.linked_order_number, "ORD-100")

    def test_unpaid_invoice_has_no_payment_date_or_value(self):
        self.order = replace(self.order, payment=replace(self.order.payment, status="UNPAID", payment_date=None))
        create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.payment_args, ("PAY1", False, None, None))

    def test_broken_order_link_stops_before_save(self):
        self.ui.invoice = replace(self.ui.invoice, linked_order_number="OTHER")
        with self.assertRaisesRegex(InvoiceReview, "not linked"):
            create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 0)

    def test_copied_total_mismatch_stops_before_save(self):
        self.ui.invoice = replace(self.ui.invoice, totals=replace(self.ui.invoice.totals, gross=Decimal("678.31")))
        with self.assertRaisesRegex(InvoiceReview, "Invoice totals"):
            create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 0)

    def test_saved_payment_mismatch_stops_without_resaving(self):
        original_save = self.ui.save_invoice

        def wrong_saved_payment():
            original_save()
            self.ui.invoice_rows = [replace(self.ui.invoice_rows[0], paid=False)]

        self.ui.save_invoice = wrong_saved_payment
        with self.assertRaisesRegex(InvoiceReview, "payment method or state"):
            create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 1)

    def test_original_order_must_remain_open(self):
        self.ui.order_rows = [replace(self.saved, state="Closed")]
        with self.assertRaisesRegex(InvoiceReview, "Original Order"):
            create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 1)

    def test_duplicate_invoice_row_does_not_trigger_second_save(self):
        original_save = self.ui.save_invoice

        def duplicate_saved_invoice():
            original_save()
            self.ui.invoice_rows.append(self.ui.invoice_rows[0])

        self.ui.save_invoice = duplicate_saved_invoice
        with self.assertRaisesRegex(InvoiceReview, "Expected one saved Invoice"):
            create_and_verify_invoice(self.order, self.saved, self.resolved, self.ui)
        self.assertEqual(self.ui.save_count, 1)


if __name__ == "__main__":
    unittest.main()
