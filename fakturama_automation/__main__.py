from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import ReviewRequired, extract
from .ocr import OCRError, read_image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract and validate one Fakturama order image")
    parser.add_argument("image", type=Path, help="PNG or JPEG sales order image")
    args = parser.parse_args(argv)
    try:
        order = extract(read_image(args.image))
    except (OCRError, ReviewRequired) as exc:
        print(json.dumps({"status": "manual_review", "reason": str(exc)}), file=sys.stdout)
        return 2
    print(json.dumps({"status": "valid", "order": order.to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
