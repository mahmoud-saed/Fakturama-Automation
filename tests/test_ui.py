import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fakturama_automation.ui import FakturamaUI, Selector, UIActionError


class FakeControl:
    def __init__(self, name, control_type="Edit", value="", enabled=True):
        self.element_info = SimpleNamespace(name=name, control_type=control_type, auto_id="")
        self.value = value
        self.enabled = enabled
        self.clicked = False

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.enabled

    def set_edit_text(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def click_input(self):
        self.clicked = True


class FakeWindow:
    def __init__(self, controls):
        self.controls = controls

    def descendants(self):
        return self.controls

    def capture_as_image(self):
        return self

    def save(self, path):
        Path(path).write_bytes(b"fake screenshot")


class UITests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.date = FakeControl("Order Date")
        self.save = FakeControl("Save", "Button")
        self.window = FakeWindow([self.date, self.save])
        self.ui = FakturamaUI(
            self.window,
            {"order_date": Selector(name="Order Date", control_type="Edit"),
             "save": Selector(name="Save", control_type="Button")},
            Path(self.folder.name), timeout=0, poll_interval=0,
        )

    def test_fill_reads_back_value(self):
        self.ui.fill("order_date", "2026-07-14", step="Enter order date")
        self.assertEqual(self.date.value, "2026-07-14")

    def test_click_can_check_result_control(self):
        self.ui.click("save", step="Save order", expect="order_date")
        self.assertTrue(self.save.clicked)

    def test_ambiguous_control_stops_with_screenshot(self):
        self.window.controls.append(FakeControl("Save", "Button"))
        with self.assertRaises(UIActionError) as raised:
            self.ui.click("save", step="Save order")
        self.assertIn("ambiguous", str(raised.exception))
        self.assertTrue(raised.exception.screenshot.is_file())

    def test_missing_selector_has_clear_reason(self):
        with self.assertRaises(UIActionError) as raised:
            self.ui.click("new_order", step="Open order")
        self.assertIn("No inspected selector", str(raised.exception))

    def test_disabled_control_stops(self):
        self.save.enabled = False
        with self.assertRaises(UIActionError) as raised:
            self.ui.click("save", step="Save order")
        self.assertIn("disabled", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
