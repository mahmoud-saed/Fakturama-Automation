# Fakturama order image extraction

The CLI reads one sales order image and validates the extracted data. A small Windows UI Automation interaction layer is also available for the later Fakturama workflow. The CLI does not open or modify Fakturama yet.

## Setup

Install Python 3.12+, [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) with English language data, and the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Tesseract must be available as `tesseract` on `PATH`.

## Run

```bash
python -m fakturama_automation path/to/order.png
```

The CLI prints JSON. A validated order has `status: valid` and exit code 0. Missing, uncertain, or inconsistent fields produce `status: manual_review`, a reason, and exit code 2. No records are changed.

The parser currently targets the supplied **Sales Order Input** image layout. Other templates need their own parser. The OCR confidence threshold is 70 for every value used. Dates are ISO (`YYYY-MM-DD`); monetary calculations use `Decimal` and two decimal places. Totals are checked against the source image before the order is accepted.

## UI interaction layer

`fakturama_automation.ui.FakturamaUI` centralizes exact control lookup, waits, clicks, text entry, read-back checks, and failure screenshots. It accepts a pywinauto UIA window plus a map of `Selector` values. A missing, disabled, or ambiguous control raises `UIActionError` with the action name and a screenshot path. Selectors are intentionally supplied by the workflow: Fakturama is not installed here, so its version, language, and actual automation IDs cannot yet be verified. The supplied screenshots show labels but do not expose automation IDs.

## Order and master-data workflow

`open_order_and_resolve(order, desktop)` validates the extracted order, opens an Order with Net prices and VAT enabled, and keeps it open while resolving the debtor, payment method, VAT, and each product. It only reuses unique exact matches. Missing records are created with the PRD defaults, searched again, and selected or checked from the open Order. Duplicates, conflicting settings, failed re-selection, or a closed Order raise `MasterDataReview` with the failing step.

The `desktop` argument implements `MasterDataPort`. Live Fakturama operations cannot be wired or verified until the Windows application is installed and its controls are inspected. The CLI still performs extraction only; it does not create records.

`complete_and_verify_order(order, resolved, desktop)` then enters and checks each selected product line in image order, compares the displayed net, VAT, and gross totals, saves once, and confirms a unique open Order row in Documents with the generated number, date, customer reference, and total. A mismatch raises `OrderReview` with its step. This function needs an `OrderCompletionPort` desktop implementation after live UI inspection.

`create_and_verify_invoice(order, saved_order, resolved, desktop)` uses the saved Order's follow-up Invoice action. It checks the copied link, addresses, dates, items, and totals; applies and reads back payment details; saves once; then verifies both the Invoice and the still-open Order in Documents. `InvoiceReview` reports the failed step. Its `InvoicePort` also needs live UI wiring after installation.

## Tests

```bash
python -m unittest discover -s tests -v
```
