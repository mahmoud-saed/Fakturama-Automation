"""Inspected SWT controls, with visual grounding only for custom tables."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from PIL import ImageOps

from .extract import ReviewRequired, _decimal, _lines, _text, _trusted
from .ocr import OCRError, read_bitmap
from .ui import FakturamaUI

LOGGER = logging.getLogger(__name__)


class WindowsControls:
    def __init__(self, window, evidence_dir: Path):
        self.window = window
        self.evidence = FakturamaUI(window, {}, evidence_dir)

    @classmethod
    def connect(cls, evidence_dir: Path):
        from pywinauto import Desktop

        matches = Desktop(backend="uia").windows(title_re=r"^Fakturama -.*")
        if len(matches) != 1:
            raise ReviewRequired(f"Expected one Fakturama window, found {len(matches)}")
        matches[0].set_focus()
        return cls(matches[0], evidence_dir)

    def fail(self, reason: str):
        raise self.evidence._fail("desktop", reason)

    def find(self, name: str, kind: str, root=None):
        root = self.window if root is None else root
        deadline = time.monotonic() + 8
        while True:
            matches = [c for c in root.descendants(control_type=kind) if c.window_text() == name and c.is_visible()]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                self.fail(f"Ambiguous {kind} {name!r}")
            if time.monotonic() >= deadline:
                self.fail(f"Missing {kind} {name!r}")
            time.sleep(.2)

    def field(self, label: str, kind: str = "Edit", root=None, index: int = 0):
        root = self.window if root is None else root
        named = [c for c in root.descendants(control_type=kind) if c.window_text() == label and c.is_visible()]
        if len(named) == 1 and index == 0:
            return named[0]
        text = self.find(label, "Text", root)
        children = text.parent().children()
        position = next(i for i, child in enumerate(children) if child == text)
        if position + 1 == len(children):
            self.fail(f"No field follows label {label!r}")
        sibling = children[position + 1]
        fields = [sibling] if sibling.element_info.control_type == kind else sibling.descendants(control_type=kind)
        if index >= len(fields):
            self.fail(f"No {kind} follows label {label!r}")
        return fields[index]

    @staticmethod
    def native(control):
        from pywinauto import Desktop

        return Desktop(backend="win32").window(handle=control.handle).wrapper_object()

    def menu(self, path: str):
        LOGGER.info("Fakturama menu: %s", path)
        self.native(self.window).menu_select(path)

    def value(self, control):
        return self.native(control).window_text().strip()

    def click(self, control, *, double=False):
        target = control if control.handle else control.parent()
        rectangle = control.rectangle()
        parent_rectangle = target.rectangle()
        self.native(target).click(
            coords=((rectangle.left + rectangle.right) // 2 - parent_rectangle.left,
                    (rectangle.top + rectangle.bottom) // 2 - parent_rectangle.top),
            double=double,
        )

    def fill(self, control, value: str, *, commit=True):
        if not control.is_enabled():
            self.fail("Cannot fill a disabled control")
        self.click(control)
        if re.fullmatch(r"-?\d+(?:[.,]\d+)?%?", value):
            control.type_keys("^a" + value.rstrip("%"), with_spaces=True)
        else:
            self.native(control).set_edit_text(value)
        if commit:
            control.type_keys("{TAB}")
        deadline = time.monotonic() + 8
        while True:
            actual = self.value(control)
            matches = actual == value.strip()
            if not matches and actual and re.fullmatch(r"-?\d+(?:[.,]\d+)?%?", value):
                matches = _decimal(actual, "field") == _decimal(value, "expected field")
            if matches:
                return
            if time.monotonic() >= deadline:
                self.fail(f"Field readback differs from {value!r}: {actual!r}")
            time.sleep(.2)

    def select(self, control, value: str):
        native = self.native(control)
        choices = native.item_texts()
        matches = [i for i, text in enumerate(choices) if text.strip() == value]
        if len(matches) != 1:
            self.fail(f"Combo option {value!r} is missing or ambiguous: {choices}")
        native.select(matches[0])
        if native.selected_text().strip() != value:
            self.fail(f"Combo did not select {value!r}")

    def pane(self, title: str):
        return self.find(title, "Pane")

    def table(self, root):
        candidates = [c for c in root.descendants(control_type="Pane")
                      if c.is_visible() and all(child.element_info.control_type == "ScrollBar" or
                             (child.element_info.control_type in ("Edit", "ComboBox") and child.rectangle().height() < 40)
                             for child in c.children())
                      and c.rectangle().width() > 300 and c.rectangle().height() > 100]
        if len(candidates) != 1:
            self.fail(f"Expected one custom table in {root.window_text()!r}, found {len(candidates)}")
        control = candidates[0]
        previous = None
        stable_since = time.monotonic()
        deadline = stable_since + 8
        while time.monotonic() < deadline:
            current = control.capture_as_image().tobytes()
            if current != previous:
                previous = current
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= .6:
                return VisualTable(self, control)
            time.sleep(.2)
        self.fail("Custom table did not settle after the action")


class VisualTable:
    """Derive cell positions from the current grid capture, never saved coordinates."""

    def __init__(self, controls: WindowsControls, control):
        self.controls = controls
        self.control = control
        self.image = control.capture_as_image().convert("RGB")
        pixels = self.image.load()
        horizontal = []
        for y in range(3, self.image.height):
            count = sum(1 for x in range(self.image.width)
                        if 80 <= pixels[x, y][0] <= 220 and max(pixels[x, y]) - min(pixels[x, y]) < 8
                        and max(abs(a - b) for a, b in zip(pixels[x, y], pixels[x, min(y + 2, self.image.height - 1)])) > 20)
            if count / self.image.width > .4 and (not horizontal or y - horizontal[-1] > 3):
                horizontal.append(y)
        if not horizontal:
            controls.fail("Could not ground the table header boundary")
        self.header_bottom = horizontal[0]
        sample_start = self.header_bottom + 3
        sample_height = self.image.height - sample_start
        if sample_height <= 0:
            controls.fail("Table capture has no body below its header")
        borders = []
        for x in range(self.image.width):
            gray = sum(1 for y in range(sample_start, self.image.height)
                       if 80 <= pixels[x, y][0] <= 220 and max(pixels[x, y]) - min(pixels[x, y]) < 8
                       and max(abs(a - b) for a, b in zip(pixels[x, y], pixels[min(x + 2, self.image.width - 1), y])) > 20)
            if gray / sample_height > .6 and (not borders or x - borders[-1] > 3):
                borders.append(x)
        if not borders:
            # Some SWT tables draw vertical separators only across the header and
            # populated row band; the large blank body dilutes the full-height scan.
            band_bottom = min(self.image.height, self.header_bottom + 24)
            band_height = max(1, band_bottom)
            for x in range(self.image.width):
                gray = sum(1 for y in range(0, band_bottom)
                           if pixels[x, y][0] < 235 and max(pixels[x, y]) - min(pixels[x, y]) < 12)
                if gray / band_height > .55 and (not borders or x - borders[-1] > 3):
                    borders.append(x)
        self.edges = sorted(set([0, *borders, self.image.width]))
        if len(self.edges) < 3:
            controls.fail("Could not ground the table column boundaries")
        self.headers = [self._cell_text(a, 0, b, self.header_bottom, required=False)
                        for a, b in zip(self.edges, self.edges[1:])]
        self.rows = []
        if len(horizontal) > 3:
            for top, bottom in zip(horizontal, horizontal[1:]):
                column = next((i for i, name in enumerate(self.headers)
                               if any(label in name.casefold() for label in ("pos.", "no.", "name", "narne", "document", "docurnent"))), 0)
                left, right = self.edges[column:column + 2]
                background = pixels[left + 3, min(top + 3, bottom - 1)]
                ink = sum(1 for y in range(top + 3, bottom - 2) for x in range(left + 3, right - 3)
                          if max(abs(a - b) for a, b in zip(pixels[x, y], background)) > 40)
                if ink > 3:
                    self.rows.append((top + 1, bottom - 1))
        else:
            page = read_bitmap(self.image)
            scale = page.width / self.image.width
            self.rows = [(min(w.y for w in line) / scale - 2, max(w.y + w.height for w in line) / scale + 2)
                         for line in _lines(page.words) if min(w.y for w in line) / scale > self.header_bottom + 1]

    def _cell_text(self, left, top, right, bottom, *, required=True, numeric=False, leading_icon=False):
        if right - left < 5 or bottom - top < 3:
            return ""
        inset = 5 if top > 0 else 3
        if leading_icon:
            # SWT state icons occupy approximately one row-height before the label.
            inset = max(inset, round((bottom - top) * .85))
        bounds = (int(left) + inset, int(top) + (5 if top > 0 else 2),
                  int(right) - 3, int(bottom) - 3)
        if bounds[2] - bounds[0] < 3 or bounds[3] - bounds[1] < 3:
            if required:
                self.controls.fail("Table cell is too small for reliable readback")
            return ""
        image = self.image.crop(bounds)
        if image.width < 3 or image.height < 3:
            return ""
        if all(extrema == (255, 255) for extrema in image.getextrema()):
            return ""
        background = max(image.getcolors(image.width * image.height), key=lambda entry: entry[0])[1]
        image = ImageOps.grayscale(image)
        if max(background) < 230 or max(background) - min(background) > 30:
            image = ImageOps.invert(image)
        image = ImageOps.autocontrast(image)
        image = ImageOps.expand(image, border=4, fill=255)
        try:
            page = read_bitmap(image, psm=7, min_width=400)
        except OCRError:
            if required:
                self.controls.fail("Table cell could not be read")
            return ""
        words = list(page.words)
        if numeric:
            words = [word for word in words if re.search(r"\d", word.text)]
            if len({_decimal(word.text, "table number") for word in words}) > 1:
                self.controls.fail("Table cell contains conflicting numeric values")
        return _trusted(words, "table cell") if required else _text(words)

    def read_text(self, index: int, column: str, *, leading_icon=False):
        matches = [i for i, name in enumerate(self.headers) if name == column]
        if len(matches) != 1:
            self.controls.fail(f"Table column {column!r} is missing or ambiguous")
        col = matches[0]
        top, bottom = self.rows[index]
        return self._cell_text(self.edges[col], top, self.edges[col + 1], bottom,
                               leading_icon=leading_icon)

    def row(self, index: int):
        top, bottom = self.rows[index]
        return [self._cell_text(a, top, b, bottom, required=False)
                for a, b in zip(self.edges, self.edges[1:])]

    def click_row(self, index: int, *, double=False):
        top, bottom = self.rows[index]
        y = (top + bottom) / 2
        column = next((i for i, name in enumerate(self.headers)
                       if any(label in name.casefold() for label in ("no.", "name", "narne", "document", "docurnent"))), None)
        if column is None:
            self.controls.fail(f"No grounded record column in {self.headers}")
        x = (self.edges[column] + self.edges[column + 1]) / 2
        self.controls.native(self.control).click(coords=(int(x), int(y)), double=double)

    def edit_cell(self, index: int, column: str, value: str):
        control = self.cell_editor(index, column)
        control.type_keys("^a" + value.rstrip("%") + "{ENTER}", with_spaces=True)
        self.controls.click(self.controls.find("Items", "Text"))
        actual = self.read_cell(index, column)
        expected = _decimal(value, column)
        if column == "Discount":
            expected = -expected
        if _decimal(actual, column) != expected:
            self.controls.fail(f"Committed {column!r} differs from {value!r}: {actual!r}")

    def cell_editor(self, index: int, column: str):
        matches = [i for i, name in enumerate(self.headers) if name.strip() == column]
        if len(matches) != 1:
            self.controls.fail(f"Table column {column!r} not found: {self.headers}")
        col = matches[0]
        top, bottom = self.rows[index]
        y = (top + bottom) / 2
        x = (self.edges[col] + self.edges[col + 1]) / 2
        self.controls.native(self.control).click(coords=(int(x), int(y)), double=True)
        rectangle = self.control.rectangle()
        screen_x, screen_y = rectangle.left + x, rectangle.top + y
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            fields = [c for c in self.control.descendants(control_type="Edit") if c.is_visible()
                      and c.rectangle().left <= screen_x < c.rectangle().right
                      and c.rectangle().top <= screen_y < c.rectangle().bottom]
            if len(fields) == 1:
                return fields[0]
            time.sleep(.1)
        self.controls.fail(f"Cell {column!r} did not expose one native editor")

    def read_cell(self, index: int, column: str):
        if column in ("VAT", "Price"):
            matches = [i for i, name in enumerate(self.headers) if name == column]
            if len(matches) != 1:
                self.controls.fail(f"Cannot ground read-only column {column!r}")
            col = matches[0]
            top, bottom = self.rows[index]
            right = self.edges[col + 1] - (14 if column == "VAT" else 0)
            return self._cell_text(self.edges[col], top, right, bottom, numeric=True)
        field = self.cell_editor(index, column)
        value = self.controls.value(field)
        self.controls.click(self.controls.find("Items", "Text"))
        return value
