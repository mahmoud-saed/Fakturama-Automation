import unittest
from decimal import Decimal
from dataclasses import replace

from fakturama_automation.extract import ReviewRequired
from fakturama_automation.master_data import (
    DebtorRecord, MasterDataReview, PaymentRecord, ProductRecord, VATRecord,
    _vat_name, open_order_and_resolve,
)
from tests.test_extraction import sample_order


class FakeDesktop:
    def __init__(self):
        self.open = False
        self.actions = []
        self.debtors = []
        self.payments = []
        self.vats = []
        self.products = []
        self.addresses_match = True
        self.created_payments = []
        self.created_products = []

    def open_order(self, spec):
        self.actions.append("open_order")
        self.open_spec = spec
        self.open = True
        return "ORD-100"

    def order_is_open(self):
        return self.open

    def find_debtors(self, query):
        self.actions.append("find_debtor")
        return self.debtors

    def create_debtor(self, spec):
        self.actions.append("create_debtor")
        self.created_debtor = spec
        self.debtors.append(DebtorRecord("D1", spec.company, spec.first_name, spec.last_name, spec.billing_address.postal_code, spec.billing_address.city))

    def select_debtor(self, record_id):
        self.actions.append("select_debtor")

    def debtor_addresses_match(self, debtor):
        return self.addresses_match

    def find_payments(self, name):
        self.actions.append("find_payment")
        return self.payments

    def create_payment(self, spec):
        self.actions.append("create_payment")
        self.created_payments.append(spec)
        self.payments.append(PaymentRecord("PAY1", spec.name, spec.description, spec.code))

    def find_vats(self, name):
        self.actions.append("find_vat")
        return self.vats

    def create_vat(self, spec):
        self.actions.append("create_vat")
        self.vats.append(VATRecord("V1", spec.name, spec.value, spec.code))

    def find_products(self, sku):
        self.actions.append("find_product")
        return [product for product in self.products if product.sku == sku]

    def create_product(self, spec):
        self.actions.append("create_product")
        self.created_products.append(spec)
        self.products.append(ProductRecord(f"P{len(self.products) + 1}", spec.sku, spec.name, spec.description, spec.vat_name, spec.gross_price))

    def select_product(self, record_id):
        self.actions.append(f"select_product:{record_id}")


class MasterDataTests(unittest.TestCase):
    def test_creates_missing_records_after_opening_order_and_reselects(self):
        ui = FakeDesktop()
        result = open_order_and_resolve(sample_order(), ui)
        self.assertEqual(result.order_number, "ORD-100")
        self.assertEqual(result.product_ids, ("P1", "P2"))
        self.assertEqual(ui.actions[0], "open_order")
        self.assertEqual(ui.actions.count("find_debtor"), 2)
        self.assertEqual(ui.actions.count("find_payment"), 2)
        self.assertEqual(ui.actions.count("find_vat"), 3)
        self.assertEqual(ui.actions.count("find_product"), 4)
        self.assertTrue(ui.open)
        self.assertEqual(ui.open_spec.price_mode, "Net")
        self.assertEqual(ui.open_spec.vat_mode, "With VAT")
        self.assertEqual(ui.created_debtor.salutation, "---")
        self.assertEqual(ui.created_debtor.discount_percent, Decimal("0"))
        self.assertFalse(ui.created_debtor.single_address)
        self.assertEqual(ui.created_payments[0].code, "Credit transfer")
        self.assertFalse(ui.created_payments[0].standard)
        self.assertEqual(ui.created_products[0].gross_price, Decimal("297.50"))
        self.assertEqual(ui.created_products[0].cost_price_net, Decimal("0.00"))

    def test_reuses_unique_matching_records(self):
        ui = FakeDesktop()
        ui.debtors = [DebtorRecord("D1", "Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin")]
        ui.payments = [PaymentRecord("PAY1", "Bank Transfer", "Bank Transfer", "Credit transfer")]
        ui.vats = [VATRecord("V1", "VAT 19%", Decimal("19"), "S (Standard rate)")]
        ui.products = [
            ProductRecord("P1", "CHR-ERG-01", "Ergonomic Desk Chair", "Ergonomic Desk Chair", "VAT 19%", Decimal("297.50")),
            ProductRecord("P2", "MAT-DESK-02", "Anti-Fatigue Desk Mat", "Anti-Fatigue Desk Mat", "VAT 19%", Decimal("47.60")),
        ]
        result = open_order_and_resolve(sample_order(), ui)
        self.assertEqual(result.product_ids, ("P1", "P2"))
        self.assertFalse(any(action.startswith("create_") for action in ui.actions))

    def test_duplicate_debtor_stops_before_creation(self):
        ui = FakeDesktop()
        row = DebtorRecord("D1", "Northstar Office GmbH", "Marta", "Klein", "10117", "Berlin")
        ui.debtors = [row, DebtorRecord("D2", row.company, row.first_name, row.last_name, row.postal_code, row.city)]
        with self.assertRaisesRegex(MasterDataReview, "Multiple debtor"):
            open_order_and_resolve(sample_order(), ui)
        self.assertNotIn("create_debtor", ui.actions)

    def test_conflicting_vat_stops_before_product_creation(self):
        ui = FakeDesktop()
        ui.vats = [VATRecord("V1", "VAT 19%", Decimal("7"), "S (Standard rate)")]
        with self.assertRaisesRegex(MasterDataReview, "conflicting"):
            open_order_and_resolve(sample_order(), ui)
        self.assertNotIn("create_product", ui.actions)

    def test_lost_order_editor_stops(self):
        ui = FakeDesktop()
        original = ui.create_debtor

        def close_after_create(debtor):
            original(debtor)
            ui.open = False

        ui.create_debtor = close_after_create
        with self.assertRaisesRegex(MasterDataReview, "no longer open"):
            open_order_and_resolve(sample_order(), ui)

    def test_invalid_source_stops_before_opening_order(self):
        ui = FakeDesktop()
        order = sample_order()
        order = replace(order, totals=replace(order.totals, gross=Decimal("1.00")))
        with self.assertRaises(ReviewRequired):
            open_order_and_resolve(order, ui)
        self.assertEqual(ui.actions, [])

    def test_unsupported_payment_stops_before_opening_order(self):
        ui = FakeDesktop()
        order = sample_order()
        order = replace(order, payment=replace(order.payment, method="Unknown Method"))
        with self.assertRaisesRegex(MasterDataReview, "Unsupported payment"):
            open_order_and_resolve(order, ui)
        self.assertEqual(ui.actions, [])

    def test_vat_name_uses_plain_decimal(self):
        self.assertEqual(_vat_name(Decimal("10")), "VAT 10%")


if __name__ == "__main__":
    unittest.main()
