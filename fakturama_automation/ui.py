"""Small, strict UI Automation helpers for the inspected Fakturama window.

The workflow supplies selectors after live inspection. This layer never guesses a
control or uses fixed screen coordinates.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Selector:
    name: str | None = None
    control_type: str | None = None
    automation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.name and not self.automation_id:
            raise ValueError("A selector needs an exact name or automation ID")


@dataclass(frozen=True)
class UIActionError(Exception):
    step: str
    reason: str
    screenshot: Path | None = None

    def __str__(self) -> str:
        result = f"{self.step}: {self.reason}"
        return f"{result} (screenshot: {self.screenshot})" if self.screenshot else result


class FakturamaUI:
    """Interact with one window through exact Windows UI Automation properties."""

    def __init__(
        self,
        window: object,
        selectors: Mapping[str, Selector],
        evidence_dir: Path,
        *,
        timeout: float = 8.0,
        poll_interval: float = 0.2,
    ) -> None:
        self.window = window
        self.selectors = dict(selectors)
        self.evidence_dir = evidence_dir
        self.timeout = timeout
        self.poll_interval = poll_interval

    def screenshot(self, step: str) -> Path:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")[:60] or "ui"
        path = self.evidence_dir / f"{safe_name}_{time.time_ns()}.png"
        self.window.capture_as_image().save(path)
        return path

    def _fail(self, step: str, reason: str) -> UIActionError:
        try:
            image = self.screenshot(step)
        except Exception as exc:
            LOGGER.warning("%s: failure screenshot could not be captured: %s", step, exc)
            image = None
        error = UIActionError(step, reason, image)
        LOGGER.error("%s", error)
        return error

    @staticmethod
    def _matches(control: object, selector: Selector) -> bool:
        info = control.element_info
        return (
            (selector.automation_id is None or info.auto_id == selector.automation_id)
            and (selector.name is None or info.name == selector.name)
            and (selector.control_type is None or info.control_type == selector.control_type)
        )

    def wait_for(self, key: str, *, step: str, timeout: float | None = None) -> object:
        selector = self.selectors.get(key)
        if selector is None:
            raise self._fail(step, f"No inspected selector is configured for {key!r}")
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        reason = f"Control {key!r} was not found"
        while True:
            try:
                matches = [control for control in self.window.descendants() if self._matches(control, selector) and control.is_visible()]
            except Exception as exc:
                raise self._fail(step, f"Could not inspect Fakturama controls: {exc}") from exc
            if len(matches) > 1:
                raise self._fail(step, f"Control {key!r} is ambiguous ({len(matches)} visible matches)")
            if len(matches) == 1:
                try:
                    if matches[0].is_enabled():
                        return matches[0]
                except Exception as exc:
                    raise self._fail(step, f"Could not check whether {key!r} is enabled: {exc}") from exc
                reason = f"Control {key!r} is disabled"
            if time.monotonic() >= deadline:
                raise self._fail(step, reason)
            time.sleep(self.poll_interval)

    def click(self, key: str, *, step: str, expect: str | None = None) -> None:
        control = self.wait_for(key, step=step)
        try:
            control.click_input()
        except Exception as exc:
            raise self._fail(step, f"Could not click {key!r}: {exc}") from exc
        if expect is not None:
            self.wait_for(expect, step=f"{step} — check result")

    def read_text(self, key: str, *, step: str) -> str:
        control = self.wait_for(key, step=step)
        try:
            getter = getattr(control, "get_value", None)
            value = getter() if callable(getter) else control.window_text()
            return str(value or "").strip()
        except Exception as exc:
            raise self._fail(step, f"Could not read {key!r}: {exc}") from exc

    def check_text(self, key: str, expected: str, *, step: str) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            actual = self.read_text(key, step=step)
            if actual == expected:
                return
            if time.monotonic() >= deadline:
                raise self._fail(step, f"{key!r} reads {actual!r}; expected {expected!r}")
            time.sleep(self.poll_interval)

    def fill(self, key: str, value: str, *, step: str) -> None:
        control = self.wait_for(key, step=step)
        setter = getattr(control, "set_edit_text", None)
        if not callable(setter):
            raise self._fail(step, f"{key!r} is not an editable text control")
        try:
            setter(value)
        except Exception as exc:
            raise self._fail(step, f"Could not enter {key!r}: {exc}") from exc
        self.check_text(key, value, step=f"{step} — check entered value")
