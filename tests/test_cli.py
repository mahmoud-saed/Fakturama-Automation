import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fakturama_automation.__main__ import main
from fakturama_automation.extract import ReviewRequired


class CLITests(unittest.TestCase):
    def test_extraction_default_never_connects_to_desktop(self):
        order = Mock(items=[1], totals=SimpleNamespace(gross=Decimal('678.30')))
        order.to_dict.return_value = {'external_reference': 'TEST'}
        output = io.StringIO()
        with patch('fakturama_automation.__main__.read_image', return_value=SimpleNamespace(words=[])), \
                patch('fakturama_automation.__main__.extract', return_value=order), \
                patch('fakturama_automation.desktop.FakturamaDesktop.connect') as connect, \
                contextlib.redirect_stdout(output):
            self.assertEqual(main(['sample.png']), 0)
        connect.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'valid')

    def test_run_executes_all_three_workflow_stages_in_order(self):
        order = Mock(items=[1], totals=SimpleNamespace(gross=Decimal('678.30')))
        desktop = Mock()
        resolved = Mock()
        saved = SimpleNamespace(number='PO123')
        invoice = SimpleNamespace(number='INV123', gross_total=Decimal('678.30'))
        events = []
        def stage(name, result):
            def run(*args):
                events.append(name)
                return result
            return run

        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            output = io.StringIO()
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(patch('fakturama_automation.__main__.sys.platform', 'win32'))
            stack.enter_context(patch('fakturama_automation.__main__.read_image', return_value=SimpleNamespace(words=[])))
            stack.enter_context(patch('fakturama_automation.__main__.extract', return_value=order))
            stack.enter_context(patch('fakturama_automation.desktop.FakturamaDesktop.connect', return_value=desktop))
            stack.enter_context(patch('fakturama_automation.__main__.open_order_and_resolve', side_effect=stage('masters', resolved)))
            stack.enter_context(patch('fakturama_automation.__main__.complete_and_verify_order', side_effect=stage('order', saved)))
            stack.enter_context(patch('fakturama_automation.__main__.create_and_verify_invoice', side_effect=stage('invoice', invoice)))
            self.assertEqual(main(['sample.png', '--run', '--evidence-dir', folder]), 0)
            self.assertTrue((Path(folder) / 'run.log').exists())
        self.assertEqual(events, ['masters', 'order', 'invoice'])
        self.assertEqual(json.loads(output.getvalue())['invoice_number'], 'INV123')

    def test_desktop_review_stops_before_order_save_and_captures_failure(self):
        order = Mock(items=[1], totals=SimpleNamespace(gross=Decimal('678.30')))
        desktop = Mock()
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch('fakturama_automation.__main__.sys.platform', 'win32'))
            stack.enter_context(patch('fakturama_automation.__main__.read_image', return_value=SimpleNamespace(words=[])))
            stack.enter_context(patch('fakturama_automation.__main__.extract', return_value=order))
            stack.enter_context(patch('fakturama_automation.desktop.FakturamaDesktop.connect', return_value=desktop))
            stack.enter_context(patch('fakturama_automation.__main__.open_order_and_resolve', side_effect=ReviewRequired('conflict')))
            complete = stack.enter_context(patch('fakturama_automation.__main__.complete_and_verify_order'))
            self.assertEqual(main(['sample.png', '--run', '--evidence-dir', folder]), 2)
        complete.assert_not_called()
        desktop.capture.assert_called_once_with('manual-review.png')
