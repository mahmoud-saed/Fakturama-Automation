"""Windows adapter for the inspected Fakturama Order-to-Invoice workflow."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .extract import _decimal
from .master_data import DebtorRecord, PaymentRecord, ProductRecord, VATRecord
from .order_completion import DocumentOrderRow, OrderLineView, OrderTotalsView
from .models import Address
from .invoice import InvoiceView, InvoiceDocumentRow
from .windows_controls import WindowsControls

LOGGER = logging.getLogger(__name__)


class FakturamaDesktop:
    def __init__(self, controls: WindowsControls):
        self.controls = controls
        self.order_tab = ""
        self.delivery_addresses = {}
        self.money_separator = None

    @classmethod
    def connect(cls, evidence_dir: Path):
        return cls(WindowsControls.connect(evidence_dir))

    def editor(self):
        def outer_tab(control):
            parent = control.parent()
            while parent and parent != self.controls.window:
                if parent.element_info.control_type == "Tab":
                    return False
                parent = parent.parent()
            return True

        tabs = [c for c in self.controls.window.descendants(control_type="Tab")
                if c.is_visible() and c.rectangle().height() > 300
                and c.window_text() not in ("Documents", "VATs", "terms of payment", "Products", "Debtors")
                and outer_tab(c)]
        if len(tabs) != 1:
            self.controls.fail("Could not uniquely identify the active editor")
        return self.controls.find(tabs[0].window_text().lstrip("*"), "Pane")

    def field(self, name, kind="Edit", index=0):
        return self.controls.field(name, kind, self.editor(), index)

    def value(self, name, index=0):
        return self.controls.value(self.field(name, index=index))

    def fill(self, name, value, index=0):
        field = self.field(name, index=index)
        self.controls.click(field)
        text = str(value)
        separator = self.money_separator if name in ("Price (gross)", "cost price (net)", "Value") else None
        if isinstance(value, Decimal) and (separator == "," or "," in self.controls.value(field)):
            text = text.replace(".", ",")
        self.controls.fill(field, text)

    def combo(self, name, value):
        self.controls.select(self.field(name, "ComboBox"), value)

    def combo_value(self, name):
        return self.controls.native(self.field(name, "ComboBox")).selected_text().strip()

    def save(self):
        self.controls.menu("File->Save")
        button = self.controls.find("Save the current contents", "Button")
        deadline = time.monotonic() + 8
        while button.is_enabled():
            if time.monotonic() >= deadline:
                self.controls.fail("Save did not complete; it will not be retried")
            time.sleep(.2)

    def return_order(self):
        tabs = [c for c in self.controls.window.descendants(control_type="TabItem")
                if c.window_text().lstrip("*") == self.order_tab]
        if len(tabs) != 1:
            self.controls.fail("The original Order tab is missing or ambiguous")
        self.controls.click(tabs[0])

    def open_order(self, spec):
        if any(c.window_text().startswith("*") for c in
               self.controls.window.descendants(control_type="TabItem")):
            self.controls.fail("Close or resolve unsaved editors before starting a new run")
        if self.search_view("Documents", spec.external_reference).rows:
            self.controls.fail(f"Documents already exist for reference {spec.external_reference!r}; refusing a duplicate")
        self.controls.menu("New->New Order")
        self.controls.find("New Order", "Pane")
        root = self.editor()
        self.order_tab = root.window_text().lstrip("*")
        number = self.value("No.")
        date = self.field("Date")
        date.type_keys(f"{{HOME}}{spec.order_date.month}{{RIGHT}}{spec.order_date.day}{{RIGHT}}{spec.order_date.year}{{TAB}}")
        parsed = datetime.strptime(self.controls.value(date), "%b %d, %Y").date()
        if parsed != spec.order_date:
            self.controls.fail("Order date did not persist in the editor")
        self.fill("Cust.Ref.", spec.external_reference)
        modes = date.parent().parent().children(control_type="ComboBox")
        if len(modes) != 1:
            self.controls.fail("Order price-mode control is ambiguous")
        self.controls.select(modes[0], spec.price_mode)
        self.combo("VAT", spec.vat_mode)
        if self.value("No.") != number:
            self.controls.fail("Generated Order number changed")
        LOGGER.info("Opened Order %s", number)
        return number

    def order_is_open(self):
        return any(c.window_text().lstrip("*") == self.order_tab
                   for c in self.controls.window.descendants(control_type="TabItem"))

    def search_view(self, view, query):
        self.controls.menu(f"Data->{view}")
        root = self.controls.pane(view)
        self.controls.fill(self.controls.field("Search:", root=root), query)
        return self.controls.table(root)

    def open_record(self, view, query):
        table = self.search_view(view, query)
        if len(table.rows) != 1:
            self.controls.fail(f"Expected one {view} record for {query!r}, found {len(table.rows)}")
        table.click_row(0, double=True)

    def address_selector(self, query):
        self.return_order()
        label = self.controls.find("Addresses", "Text", self.editor())
        self.controls.native(label.parent().children(control_type="Image")[0]).click()
        dialog = self.controls.find("Select the address", "Window")
        edits = dialog.descendants(control_type="Edit")
        if len(edits) != 1:
            self.controls.fail("Address search field is ambiguous")
        self.controls.fill(edits[0], query, commit=False)
        return dialog, self.controls.table(dialog)

    def find_debtors(self, company_or_name):
        dialog, table = self.address_selector(company_or_name)
        candidate_count = len(table.rows)
        self.controls.click(self.controls.find("Cancel", "Button", dialog))
        records = []
        table = self.search_view("Debtors", company_or_name)
        if len(table.rows) != candidate_count:
            self.controls.fail("Debtor selector and Debtors view show different candidate counts")
        for index in range(candidate_count):
            table.click_row(index, double=True)
            self.controls.click(self.controls.find("Addresses", "TabItem", self.editor()))
            self.controls.click(self.controls.find("Main address", "TabItem", self.editor()))
            records.append(DebtorRecord(
                self.value("Customer ID"), self.value("Company"),
                self.value("First Name Last Name", 0), self.value("First Name Last Name", 1),
                self.value("ZIP - City", 0), self.value("ZIP - City", 1),
            ))
            additional = [c for c in self.editor().descendants(control_type="TabItem")
                          if c.window_text() == "additional address #1"]
            if additional:
                self.controls.click(additional[0])
                self.delivery_addresses[records[-1].record_id] = Address(
                    self.value("additional name") or records[-1].company, self.value("Street"),
                    self.value("ZIP - City", 0), self.value("ZIP - City", 1), self.combo_value("Country"))
        self.return_order()
        return records

    def address_roles(self, invoice, delivery):
        label = self.controls.find("address type", "Text", self.editor())
        siblings = label.parent().children()
        position = next(i for i, c in enumerate(siblings) if c == label)
        self.controls.click(siblings[position + 1].descendants(control_type="Button")[0])
        for name, expected in (("Invoice address", invoice), ("Delivery address", delivery)):
            box = self.controls.find(name, "CheckBox")
            if bool(box.get_toggle_state()) != expected:
                self.controls.click(box)
            if bool(box.get_toggle_state()) != expected:
                self.controls.fail(f"Address role {name!r} did not change")
        self.controls.click(self.field("Street"))

    def fill_address(self, address, email="", phone="", additional_name=""):
        for name, value in (("additional name", additional_name), ("Street", address.street),
                            ("E-Mail", email), ("Telephone", phone)):
            self.fill(name, value)
        self.fill("ZIP - City", address.postal_code, 0)
        self.fill("ZIP - City", address.city, 1)
        self.combo("Country", address.country)

    def create_debtor(self, spec):
        LOGGER.info("Creating debtor: %s", spec.company)
        self.controls.menu("New->New Debtor")
        self.controls.find("Customer ID", "Text")
        identifier = self.value("Customer ID")
        self.fill("Company", spec.company)
        self.fill("First Name Last Name", spec.first_name, 0)
        self.fill("First Name Last Name", spec.last_name, 1)
        self.combo("Salutation", spec.salutation)
        self.fill_address(spec.billing_address, spec.email, spec.phone)
        self.address_roles(True, spec.single_address)
        if not spec.single_address:
            self.controls.click(self.controls.find("+", "Button", self.editor()))
            self.fill_address(spec.delivery_address, additional_name=spec.delivery_address.company)
            self.address_roles(False, True)
        self.controls.click(self.controls.find("Miscellaneous", "TabItem", self.editor()))
        self.fill("Alias name", spec.alias)
        self.fill("Discount", "0%")
        self.combo("Net or Gross", spec.price_mode)
        if self.value("Customer ID") != identifier:
            self.controls.fail("Generated Customer ID changed")
        self.save()
        self.return_order()

    def select_debtor(self, record_id):
        self.return_order()
        self.controls.click(self.controls.find("Invoice address", "TabItem", self.editor()))
        dialog, table = self.address_selector(record_id)
        if len(table.rows) != 1:
            self.controls.fail("Debtor selection is missing or ambiguous")
        table.click_row(0)
        self.controls.click(self.controls.find("OK", "Button", dialog))
        self.selected_debtor = record_id

    def debtor_addresses_match(self, debtor):
        self.return_order()
        for tab, address in (("Invoice address", debtor.billing_address), ("Delivery address", debtor.delivery_address)):
            if (tab == "Delivery address"
                    and self.delivery_addresses.get(self.selected_debtor) == address):
                self.controls.click(self.controls.find(tab, "TabItem", self.editor()))
                root = self.controls.find(tab, "Tab", self.editor())
                fields = root.descendants(control_type="Edit")
                if len(fields) != 1:
                    self.controls.fail("Delivery address block is ambiguous")
                rendered = "\r\n".join((address.company, debtor.contact_name, address.street,
                                         f"{address.postal_code} {address.city}", address.country))
                self.controls.fill(fields[0], rendered)
            if self.read_address(tab) != address:
                return False
        return True

    def find_payments(self, name):
        table = self.search_view("terms of payment", name)
        records = []
        for index in range(len(table.rows)):
            table.click_row(index, double=True)
            records.append(PaymentRecord(self.value("Name"), self.value("Name"), self.value("Description"),
                                         self.combo_value("!editorPaymentPaymentcode!")))
        self.return_order()
        return records

    def create_payment(self, spec):
        LOGGER.info("Creating payment method: %s", spec.name)
        self.controls.menu("New->New Term of Payment")
        self.controls.find("!editorPaymentPaymentcode!", "Text")
        for name, value in (("Name", spec.name), ("Description", spec.description), ("Cash discount", "0%"),
                            ("Discount Days", "0"), ("Net Days", "0"), ("Text 'unpaid'", ""),
                            ("Text 'deposit'", ""), ("Text 'paid'", "")):
            self.fill(name, value)
        self.combo("!editorPaymentPaymentcode!", spec.code)
        self.save()
        self.return_order()

    def find_vats(self, name):
        table = self.search_view("VATs", name)
        records = []
        for index in range(len(table.rows)):
            table.click_row(index, double=True)
            records.append(VATRecord(self.value("Name"), self.value("Name"), _decimal(self.value("Value"), "VAT"),
                                     self.combo_value("VAT code (E-Invoice)")))
        self.return_order()
        return records

    def create_vat(self, spec):
        LOGGER.info("Creating VAT: %s", spec.name)
        self.controls.menu("New->New VAT")
        self.controls.find("VAT code (E-Invoice)", "Text")
        self.fill("Name", spec.name)
        self.fill("Description", spec.description)
        self.combo("VAT code (E-Invoice)", spec.code)
        self.fill("Value", str(spec.value) + "%")
        self.save()
        self.return_order()

    def find_products(self, sku):
        table = self.search_view("Products", sku)
        records = []
        for index in range(len(table.rows)):
            table.click_row(index, double=True)
            records.append(ProductRecord(self.value("Item Number"), self.value("Item Number"), self.value("Name"),
                                         self.value("Description"), self.combo_value("VAT"),
                                         _decimal(self.value("Price (gross)"), "product price")))
        self.return_order()
        return records

    def create_product(self, spec):
        LOGGER.info("Creating product: %s", spec.sku)
        self.money_separator = "," if "," in self.value("Total") else "."
        buttons = [c for c in self.controls.window.descendants(control_type="Button")
                   if c.window_text() == "Create a new product" and c.is_visible()
                   and c.rectangle().bottom < self.editor().rectangle().top]
        if len(buttons) != 1:
            self.controls.fail("Main product toolbar action is ambiguous")
        self.controls.click(buttons[0])
        self.controls.find("Item Number", "Text")
        self.combo("VAT", spec.vat_name)
        for name, value in (("Item Number", spec.sku), ("Name", spec.name), ("Description", spec.description),
                            ("Price (gross)", spec.gross_price), ("cost price (net)", spec.cost_price_net), ("Stock", spec.stock)):
            self.fill(name, value)
        self.save()
        self.return_order()

    def select_product(self, record_id):
        from pywinauto import handleprops

        self.return_order()
        previous_count = self.order_line_count()
        label = self.controls.find("Items", "Text", self.editor())
        self.controls.native(label.parent().children(control_type="Image")[0]).click()
        dialog = self.controls.find("Select a product", "Window")
        fields = [c for c in dialog.descendants(control_type="Edit") if c.is_visible()]
        if len(fields) != 1:
            self.controls.fail("Product search field is missing or ambiguous")
        # An exact SKU can be accepted synchronously by SWT's search listener.
        self.controls.native(fields[0]).set_edit_text(record_id)
        previous = None
        stable_since = time.monotonic()
        deadline = stable_since + 8
        while handleprops.iswindow(dialog.handle):
            current = dialog.capture_as_image().tobytes()
            if current != previous:
                previous = current
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= .6:
                break
            if time.monotonic() >= deadline:
                self.controls.fail("Product selector did not settle after searching")
            time.sleep(.1)
        if not handleprops.iswindow(dialog.handle):
            table = self.controls.table(self.editor())
            if len(table.rows) != previous_count + 1 or table.read_cell(previous_count, "Item No.") != record_id:
                self.controls.fail("Automatic product selection did not add exactly the requested SKU")
            return
        if self.controls.value(fields[0]) != record_id:
            self.controls.fail("Product search text differs from the requested SKU")
        table = self.controls.table(dialog)
        if len(table.rows) != 1:
            self.controls.fail("Product selection is missing or ambiguous")
        table.click_row(0)
        self.controls.click(self.controls.find("OK", "Button", dialog))

    def order_line_count(self):
        return len(self.controls.table(self.editor()).rows)

    def fill_order_line(self, index, item):
        table = self.controls.table(self.editor())
        for column, value in (("Qty.", item.quantity), ("U.Price", item.unit_net_price),
                              ("Discount", item.discount_percent)):
            current = table.read_cell(index, column)
            text = format(value, "f")
            if "," in current:
                text = text.replace(".", ",")
            table.edit_cell(index, column, text)

    def read_order_line(self, index):
        table = self.controls.table(self.editor())
        def cell(column):
            return table.read_cell(index, column)

        return OrderLineView(cell("Item No."), _decimal(cell("Qty."), "quantity"),
                             _decimal(cell("U.Price"), "unit price"),
                             _decimal(cell("VAT"), "VAT"), -_decimal(cell("Discount"), "discount"),
                             _decimal(cell("Price"), "line total"))

    def read_order_totals(self):
        shipping = [c for c in self.field("Shipping", "ComboBox").parent().children(control_type="Edit")
                    if c.window_text() == ""]
        if len(shipping) != 1:
            self.controls.fail("Shipping amount field is ambiguous")
        return OrderTotalsView(_decimal(self.value("Total Net"), "net total"),
                               _decimal(self.value("VAT"), "VAT total"),
                               _decimal(self.value("Total"), "gross total"),
                               _decimal(self.value("Discount"), "overall discount"),
                               _decimal(self.controls.value(shipping[0]), "shipping"))

    def save_order(self):
        self.save()
        self.order_tab = self.editor().window_text().lstrip("*")
        self.capture("saved-order.png")

    def capture(self, filename):
        folder = self.controls.evidence.evidence_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            self.controls.window.capture_as_image().save(folder / filename)
        except Exception as exc:
            LOGGER.warning("Evidence screenshot could not be captured: %s", exc)
            return False
        LOGGER.info("Evidence screenshot: %s", folder / filename)
        return True

    def open_documents(self):
        self.controls.menu("Data->Documents")

    def read_date(self, name):
        return datetime.strptime(self.value(name), "%b %d, %Y").date()

    def find_order_rows(self, number):
        table = self.search_view("Documents", number)
        if not table.rows:
            return []
        if len(table.rows) != 1:
            self.controls.fail(f"Multiple documents match {number!r}")
        state = table.read_text(0, "State", leading_icon=True)
        table.click_row(0, double=True)
        if self.value("No.") != number:
            self.controls.fail("Reopened document number differs from the requested Order")
        return [DocumentOrderRow(number, self.read_date("Date"), self.value("Cust.Ref."), state,
                                 _decimal(self.value("Total"), "saved Order total"))]

    def create_follow_up_invoice(self, order_number):
        rows = self.find_order_rows(order_number)
        if len(rows) != 1:
            self.controls.fail("The saved source Order could not be reopened")
        group = self.controls.find("Create a follow-up document", "Group", self.editor())
        button = self.controls.find("Invoice", "Button", group)
        if not button.is_enabled():
            self.controls.fail("Order follow-up Invoice action is disabled")
        self.controls.click(button)
        self.controls.find("Service date", "Text")
        self.linked_order_number = order_number

    def transaction_numbers(self):
        root = self.controls.pane("Documents")
        self.controls.fill(self.controls.field("Search:", root=root), "")
        self.controls.click(self.controls.find("This transaction", "TreeItem", root), double=True)
        table = self.controls.table(root)
        return [table.read_text(index, "Document") for index in range(len(table.rows))]

    def read_address(self, tab):
        self.controls.click(self.controls.find(tab, "TabItem", self.editor()))
        root = self.controls.find(tab, "Tab", self.editor())
        fields = root.descendants(control_type="Edit")
        if len(fields) != 1:
            self.controls.fail(f"Address block {tab!r} is ambiguous")
        lines = self.controls.value(fields[0]).splitlines()
        if len(lines) < 4:
            self.controls.fail(f"Address block {tab!r} is incomplete")
        location = re.fullmatch(r"(?:[A-Z]{2}-)?(\S+)\s+(.+)", lines[-2])
        if location is None:
            self.controls.fail(f"Address location {lines[-2]!r} could not be parsed")
        return Address(lines[0], lines[-3], location[1], location[2], lines[-1])

    def payment_combo(self):
        paid = self.controls.find("paid", "CheckBox", self.editor())
        children = paid.parent().children()
        index = children.index(paid)
        choices = children[index + 1].descendants(control_type="ComboBox")
        if len(choices) != 1:
            self.controls.fail("Invoice payment-method combo is ambiguous")
        return paid, choices[0]

    def read_invoice(self):
        number = self.value("No.")
        numbers = self.transaction_numbers()
        if numbers.count(self.linked_order_number) != 1:
            self.controls.fail("Invoice transaction does not contain exactly one source Order")
        paid, combo = self.payment_combo()
        is_paid = bool(paid.get_toggle_state())
        return InvoiceView(number, self.read_date("Date"), self.read_date("Service date"),
                           self.linked_order_number, self.value("Cust.Ref."), self.read_date("Order Date"),
                           self.read_address("Invoice address"), self.read_address("Delivery address"),
                           self.combo_value("VAT"),
                           tuple(self.read_order_line(i) for i in range(self.order_line_count())),
                           self.read_order_totals(), self.controls.native(combo).selected_text().strip(),
                           is_paid, self.read_date("at") if is_paid else None,
                           _decimal(self.value("Value"), "payment value") if is_paid else None)

    def set_invoice_payment(self, payment_id, paid, payment_date, value):
        box, combo = self.payment_combo()
        self.controls.select(combo, payment_id)
        if bool(box.get_toggle_state()) != paid:
            self.controls.click(box)
        if bool(box.get_toggle_state()) != paid:
            self.controls.fail("Invoice paid checkbox did not persist")
        if paid:
            field = self.field("at")
            field.type_keys(f"{{HOME}}{payment_date.month}{{RIGHT}}{payment_date.day}{{RIGHT}}{payment_date.year}{{TAB}}")
            if self.read_date("at") != payment_date:
                self.controls.fail("Invoice payment date did not persist")
            self.money_separator = "," if "," in self.value("Total") else "."
            self.fill("Value", value)

    def save_invoice(self):
        self.save()
        self.capture("saved-invoice.png")

    def find_invoice_rows(self, number):
        table = self.search_view("Documents", number)
        if not table.rows:
            return []
        if len(table.rows) != 1:
            self.controls.fail("Saved Invoice selection is ambiguous")
        table.click_row(0, double=True)
        if self.value("No.") != number:
            self.controls.fail("Reopened document is not the requested Invoice")
        invoice = self.read_invoice()
        numbers = self.transaction_numbers()
        if numbers.count(number) != 1:
            self.controls.fail("Saved Invoice is missing or duplicated in its transaction")
        self.capture("verified-invoice.png")
        return [InvoiceDocumentRow(number, invoice.invoice_date, invoice.service_date,
                                   invoice.linked_order_number, invoice.external_reference,
                                   invoice.totals.gross, invoice.payment_method, invoice.paid,
                                   invoice.payment_date, invoice.payment_value)]
