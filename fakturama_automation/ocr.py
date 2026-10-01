from __future__ import annotations

import csv
import io
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance


@dataclass(frozen=True)
class Word:
    text: str
    confidence: float
    x: int
    y: int
    width: int
    height: int

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


@dataclass(frozen=True)
class Page:
    width: int
    height: int
    words: tuple[Word, ...]


class OCRError(Exception):
    pass


def read_image(path: Path) -> Page:
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        raise OCRError("Expected a PNG or JPEG image")
    if not path.is_file():
        raise OCRError(f"Image not found: {path}")
    if not shutil.which("tesseract"):
        raise OCRError("Tesseract is not installed or is missing from PATH")

    try:
        with Image.open(path) as original:
            if original.format not in {"PNG", "JPEG"}:
                raise OCRError("File contents are not PNG or JPEG")
            return read_bitmap(original)
    except (OSError, ValueError) as exc:
        raise OCRError(f"Cannot read image: {exc}") from exc



def read_bitmap(original: Image.Image, *, psm: int = 6, min_width: int = 2000) -> Page:
    """Recognize a source image or a live control capture using the same OCR engine."""
    image = original.convert("RGB")
    if image.width < min_width:
        scale = (min_width + image.width - 1) // image.width
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
    image = ImageEnhance.Contrast(image).enhance(1.5)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    try:
        result = subprocess.run(
            ["tesseract", "stdin", "stdout", "--psm", str(psm), "-l", "eng", "tsv"],
            input=buffer.getvalue(), capture_output=True, timeout=60, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRError("OCR timed out") from exc
    if result.returncode:
        raise OCRError(result.stderr.decode("utf-8", "replace").strip() or "OCR failed")

    words = []
    for row in csv.DictReader(io.StringIO(result.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        value = row.get("text", "").strip()
        if not value:
            continue
        try:
            words.append(Word(value, float(row["conf"]), int(row["left"]), int(row["top"]), int(row["width"]), int(row["height"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise OCRError("Invalid Tesseract output") from exc
    if not words:
        raise OCRError("No text was found in the image")
    return Page(image.width, image.height, tuple(words))
