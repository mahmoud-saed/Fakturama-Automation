import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageOps

from fakturama_automation.desktop import FakturamaDesktop
from fakturama_automation.windows_controls import VisualTable, WindowsControls
from fakturama_automation.extract import ReviewRequired
from fakturama_automation.master_data import OpenOrderSpec
from fakturama_automation.ocr import Page, Word
from fakturama_automation.models import Address


class DesktopTests(unittest.TestCase):
    def test_automatic_sku_selection_requires_one_new_matching_row(self):
        import sys
        from types import SimpleNamespace

        for rows, sku, accepted in (([object()], 'CHR-ERG-01', True),
                                    ([], 'CHR-ERG-01', False),
                                    ([object(), object()], 'CHR-ERG-01', False),
                                    ([object()], 'WRONG-SKU', False)):
            with self.subTest(count=len(rows), sku=sku):
                controls = Mock()
                controls.fail.side_effect = ReviewRequired
                label, dialog, field = Mock(), Mock(handle=123), Mock()
                label.parent.return_value.children.return_value = [Mock()]
                dialog.descendants.return_value = [field]
                controls.find.side_effect = [label, dialog]
                controls.table.return_value = Mock(rows=rows)
                controls.table.return_value.read_cell.return_value = sku
                desktop = FakturamaDesktop(controls)
                api = SimpleNamespace(handleprops=Mock())
                api.handleprops.iswindow.return_value = False
                with patch.dict(sys.modules, {'pywinauto': api}), \
                        patch.object(desktop, 'return_order'), patch.object(desktop, 'editor'), \
                        patch.object(desktop, 'order_line_count', return_value=0):
                    if accepted:
                        desktop.select_product('CHR-ERG-01')
                    else:
                        with self.assertRaises(ReviewRequired):
                            desktop.select_product('CHR-ERG-01')

    def test_modal_search_readback_does_not_send_navigation_keys(self):
        controls = WindowsControls(Mock(), Path('evidence'))
        field = Mock()
        native = Mock()
        with patch.object(controls, 'click'), patch.object(controls, 'native', return_value=native), \
                patch.object(controls, 'value', return_value='CHR-ERG-01'):
            controls.fill(field, 'CHR-ERG-01', commit=False)
        native.set_edit_text.assert_called_once_with('CHR-ERG-01')
        field.type_keys.assert_not_called()

    def test_native_click_uses_current_control_handle_after_window_moves(self):
        from types import SimpleNamespace

        controls = WindowsControls(Mock(), Path('evidence'))
        control = Mock(handle=123)
        control.rectangle.return_value = SimpleNamespace(left=1500, top=250, right=1520, bottom=270)
        native = Mock()
        with patch.object(controls, 'native', return_value=native) as lookup:
            controls.click(control)
        lookup.assert_called_once_with(control)
        native.click.assert_called_once_with(coords=(10, 10), double=False)
        control.click_input.assert_not_called()

    def test_handleless_tab_click_is_relative_to_current_parent(self):
        from types import SimpleNamespace

        controls = WindowsControls(Mock(), Path('evidence'))
        control = Mock(handle=None)
        control.rectangle.return_value = SimpleNamespace(left=1550, top=150, right=1650, bottom=170)
        parent = control.parent.return_value
        parent.rectangle.return_value = SimpleNamespace(left=1500, top=120, right=2000, bottom=600)
        native = Mock()
        with patch.object(controls, 'native', return_value=native) as lookup:
            controls.click(control, double=True)
        lookup.assert_called_once_with(parent)
        native.click.assert_called_once_with(coords=(100, 40), double=True)

    def test_state_crop_excludes_icon_without_lowering_label_confidence(self):
        table = VisualTable.__new__(VisualTable)
        table.image = Image.new('RGB', (100, 40), 'white')
        drawing = ImageDraw.Draw(table.image)
        drawing.rectangle((5, 25, 14, 30), fill='black')
        drawing.rectangle((25, 25, 40, 30), fill='black')
        table.controls = Mock()

        def recognize(image, **kwargs):
            self.assertEqual(ImageOps.invert(image).getbbox()[0], 13)
            return Page(100, 40, (Word('open', 95, 0, 0, 10, 10),))

        with patch('fakturama_automation.windows_controls.read_bitmap', side_effect=recognize):
            self.assertEqual(table._cell_text(0, 19, 100, 38, leading_icon=True), 'open')
        with patch('fakturama_automation.windows_controls.read_bitmap', return_value=Page(
                100, 40, (Word('open', 69, 0, 0, 10, 10),))):
            with self.assertRaises(ReviewRequired):
                table._cell_text(0, 19, 100, 38, leading_icon=True)

    def test_saved_order_state_uses_icon_aware_readback(self):
        from datetime import date

        desktop = FakturamaDesktop(Mock())
        table = Mock(rows=[(19, 38)])
        table.read_text.return_value = 'open'
        with patch.object(desktop, 'search_view', return_value=table), \
                patch.object(desktop, 'value', side_effect=['PO123', 'REF123', '678,30']), \
                patch.object(desktop, 'read_date', return_value=date(2026, 7, 14)):
            rows = desktop.find_order_rows('PO123')
        table.read_text.assert_called_once_with(0, 'State', leading_icon=True)
        self.assertEqual(rows[0].state, 'open')

    def test_address_substrings_do_not_count_as_an_exact_match(self):
        expected = Address('Company', 'Street 1', '10117', 'Berlin', 'Germany')
        wrong = Address('Company', 'Street 10', '10117', 'Berlin', 'Germany')
        desktop = FakturamaDesktop(Mock())
        desktop.selected_debtor = 'CUST1'
        debtor = Mock(billing_address=expected, delivery_address=expected)
        with patch.object(desktop, 'return_order'), patch.object(desktop, 'editor'), \
                patch.object(desktop, 'read_address', return_value=wrong):
            self.assertFalse(desktop.debtor_addresses_match(debtor))

    def test_evidence_failure_does_not_turn_saved_document_into_an_action_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            controls = Mock()
            controls.evidence.evidence_dir = Path(folder)
            controls.window.capture_as_image.side_effect = OSError('capture unavailable')
            desktop = FakturamaDesktop(controls)
            with self.assertLogs('fakturama_automation.desktop', level='WARNING') as logs:
                self.assertFalse(desktop.capture('saved-invoice.png'))
        self.assertIn('capture unavailable', logs.output[0])

    def test_existing_reference_is_rejected_before_opening_an_order(self):
        from datetime import date

        controls = Mock()
        controls.window.descendants.return_value = []
        controls.fail.side_effect = ReviewRequired
        desktop = FakturamaDesktop(controls)
        with patch.object(desktop, 'search_view', return_value=Mock(rows=[object()])):
            with self.assertRaises(ReviewRequired):
                desktop.open_order(OpenOrderSpec(date(2026, 7, 14), 'WEB-EXISTS'))
        controls.menu.assert_not_called()

    def test_numeric_ocr_ignores_decorative_label_but_not_uncertain_rates(self):
        table = VisualTable.__new__(VisualTable)
        table.image = Image.new('RGB', (100, 30), (200, 200, 200))
        table.controls = Mock()
        table.controls.fail.side_effect = AssertionError
        label = Word('VAT', 10, 0, 0, 10, 10)
        rate = Word('19%', 95, 20, 0, 10, 10)
        with patch('fakturama_automation.windows_controls.read_bitmap', return_value=Page(100, 30, (label, rate))):
            self.assertEqual(table._cell_text(0, 1, 100, 30, numeric=True), '19%')
        uncertain = Word('19%', 69, 20, 0, 10, 10)
        with patch('fakturama_automation.windows_controls.read_bitmap', return_value=Page(100, 30, (uncertain,))):
            with self.assertRaises(ReviewRequired):
                table._cell_text(0, 1, 100, 30, numeric=True)

    def test_selected_order_grid_has_two_rows_and_body_column_boundaries(self):
        headers = ['Pos.', 'Qty.', 'Item No.', 'Picture', 'Name', 'Description',
                   'VAT', 'U.Price', 'Discount', 'Price', '']
        with Image.open(Path(__file__).parent / 'fixtures' / 'order-grid.png') as image:
            control = Mock()
            control.capture_as_image.return_value = image.convert('RGB')
            controls = Mock()
            controls.fail.side_effect = AssertionError
            with patch.object(VisualTable, '_cell_text', side_effect=headers):
                table = VisualTable(controls, control)
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.edges[:4], [0, 39, 139, 239])
        self.assertEqual(table.headers, headers)

    def test_blank_product_price_uses_order_currency_separator(self):
        from decimal import Decimal

        controls = Mock()
        controls.value.return_value = '0'
        desktop = FakturamaDesktop(controls)
        desktop.money_separator = ','
        field = Mock()
        with patch.object(desktop, 'field', return_value=field):
            desktop.fill('Price (gross)', Decimal('47.60'))
        controls.fill.assert_called_once_with(field, '47,60')

    def test_shipping_reads_amount_not_combo_text(self):
        controls = Mock()
        amount = Mock()
        amount.window_text.return_value = ''
        named = Mock()
        named.window_text.return_value = 'Discount'
        combo = Mock()
        combo.parent.return_value.children.return_value = [named, amount]
        controls.value.return_value = '0,00'
        desktop = FakturamaDesktop(controls)
        with patch.object(desktop, 'field', return_value=combo), patch.object(
                desktop, 'value', side_effect=['570,00', '108,30', '678,30', '0%']):
            totals = desktop.read_order_totals()
        self.assertEqual(str(totals.gross), '678.30')
        self.assertEqual(totals.shipping, 0)


if __name__ == '__main__':
    unittest.main()
