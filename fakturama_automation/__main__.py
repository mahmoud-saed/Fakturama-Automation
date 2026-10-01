from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .extract import ReviewRequired, extract
from .ocr import OCRError, read_image
from .master_data import MasterDataReview, open_order_and_resolve
from .order_completion import OrderReview, complete_and_verify_order
from .invoice import InvoiceReview, create_and_verify_invoice
from .ui import UIActionError

LOGGER = logging.getLogger("fakturama_automation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract and validate one Fakturama order image")
    parser.add_argument("image", type=Path, help="PNG or JPEG sales order image")
    parser.add_argument("--run", action="store_true", help="Create the Order and linked Invoice in the running Fakturama")
    parser.add_argument("--evidence-dir", type=Path, default=Path("evidence/live-run"), help="Desktop logs and screenshots directory")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Log verbosity for progress and review decisions",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[%(levelname)s] %(message)s",
        stream=sys.stderr,
    )
    LOGGER.info("Reading order image: %s", args.image)
    try:
        page = read_image(args.image)
        LOGGER.info("OCR completed: %s words detected", len(page.words))
        order = extract(page)
    except (OCRError, ReviewRequired) as exc:
        LOGGER.error("Manual review required: %s", exc)
        print(json.dumps({"status": "manual_review", "reason": str(exc)}), file=sys.stdout)
        return 2
    LOGGER.info("Extraction valid: %s item(s), gross total %s", len(order.items), order.totals.gross)
    if args.run:
        if sys.platform != "win32":
            print(json.dumps({"status": "manual_review", "reason": "Desktop automation requires Windows"}))
            return 2
        from .desktop import FakturamaDesktop

        args.evidence_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(args.evidence_dir / "run.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(handler)
        desktop = None
        try:
            desktop = FakturamaDesktop.connect(args.evidence_dir)
            LOGGER.info("Resolving master data from the open Order")
            resolved = open_order_and_resolve(order, desktop)
            LOGGER.info("Completing and verifying Order %s", resolved.order_number)
            saved = complete_and_verify_order(order, resolved, desktop)
            LOGGER.info("Creating the linked Invoice from Order %s", saved.number)
            invoice = create_and_verify_invoice(order, saved, resolved, desktop)
            LOGGER.info("Verified Order %s and Invoice %s, gross %s", saved.number, invoice.number, invoice.gross_total)
            print(json.dumps({"status": "completed", "order_number": saved.number,
                              "invoice_number": invoice.number, "gross_total": str(invoice.gross_total)}))
            return 0
        except Exception as exc:
            expected = isinstance(exc, (OCRError, ReviewRequired, MasterDataReview, OrderReview, InvoiceReview, UIActionError))
            LOGGER.error("Desktop stopped for manual review: %s", exc, exc_info=not expected)
            if desktop is not None and not isinstance(exc, UIActionError):
                try:
                    desktop.capture("manual-review.png")
                except Exception as capture_error:
                    LOGGER.warning("Failure screenshot could not be captured: %s", capture_error)
            print(json.dumps({"status": "manual_review", "reason": str(exc)}))
            return 2
        finally:
            logging.getLogger().removeHandler(handler)
            handler.close()
    print(json.dumps({"status": "valid", "order": order.to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
