# Annotated Run Evidence

Recorded on 2026-10-01 on Windows with Python 3.10, Pillow 11.3.0,
and pywinauto 0.6.9 installed in `.venv`.

## Current Verification Status

The Windows adapter is implemented. Order `PO000002` and linked paid Invoice
`INV000001` were created in stages during debugging. A later read-only check
reopened the saved records and verified their copied data, payment, transaction
membership, and the original Order remaining Open. The check did not repeat
creation or save actions.

This is **staged live verification**, not a successful uninterrupted `--run`
from an empty workspace. That final acceptance check remains outstanding.
The latest executed test suite passed 52 tests. The three primary submission
images below are retained, together with the latest acceptance failure evidence.

The latest acceptance attempt filled and validated Order `PO000005`, then stopped
after the save request with "Save did not complete; it will not be retried".
Order persistence is uncertain, and linked Invoice creation was not reached.
See [acceptance run.log](acceptance-run/run.log) and the
[save-timeout screenshot](acceptance-run/desktop_1790886478804934700.png).
Earlier debug captures were removed; their historical log paths may no longer exist.

## Submission Images and Captions

Use these in order. Each filename is immediately followed by its short caption;
place that caption beside the corresponding image in the submission.

1. [verified-documents.png](live-run/verified-documents.png) - Order `PO000002` has the source date, reference, delivery address, two items and EUR 678.30 total. The same transaction lists the Order as Open and linked Invoice `INV000001` as Paid.
2. [saved-invoice.png](live-run/saved-invoice.png) - Saved Invoice `INV000001` copies the Order's items and totals, retains generated Invoice/service dates, and records Bank Transfer payment of EUR 678.30 on July 18, 2026.
3. [desktop_1790880781276177700.png](live-run/desktop_1790880781276177700.png) - Failure evidence: automation refused to start with unsaved editors. The Invoice tab's asterisk shows the dirty editor; `run.log` records the stop reason.

The first two images show the staged successful result. The third must be paired
with its error entry in [run.log](live-run/run.log): the screenshot shows the UI
state, not an error dialog. The visible Error panel contains earlier product-menu
errors, not the reason for this safeguard stop. No image proves an uninterrupted
creation run from an empty workspace.

The log also records saved-record readback of structured addresses, line values,
payment values, and the final PASS message. Redundant debug captures were removed;
some older screenshot paths in the historical log consequently no longer exist.

The final readback verified:

- Billing: Northstar Office GmbH, Friedrichstrasse 88, 10117 Berlin, Germany.
- Delivery: Northstar Office Warehouse, Beusselstrasse 44, 10553 Berlin, Germany.
- `CHR-ERG-01`: quantity 2, unit net 250.00, discount 10%, VAT 19%, line net 450.00.
- `MAT-DESK-02`: quantity 3, unit net 40.00, discount 0%, VAT 19%, line net 120.00.
- Overall discount and shipping: zero. Net 570.00, VAT 108.30, gross 678.30 EUR.
- Bank Transfer, Paid, payment date July 18, 2026, payment value 678.30 EUR.

Fakturama displays the chair discount as `-10.00%`; the adapter normalizes
that display convention to the source's positive discount percentage and
checks the resulting line total. Structured addresses and payment values are
verified by native readback; one screenshot need not display both address tabs.

The earlier inspection also saved a separate zero-total Order `PO000001`.
It is not the source Order and is not evidence of successful completion. The
final transaction membership check excludes it. No record cleanup is claimed.

For the pending acceptance run, use a separate empty test workspace, resolve
unsaved editors, and run the README's `--run` command once. Do not recreate the
verified documents in the current workspace. Visual-grounded controls are
documented in [CONTROL_GROUNDING.md](../docs/CONTROL_GROUNDING.md).

## Earlier Automated Verification

After the real-image extraction fix, all 36 tests passed. The latest suite
subsequently reached 52 passing tests. The bundled 34-test transcript predates
the recorded-OCR and desktop/CLI regression tests.

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The older 34-test result is recorded in [test-run.txt](test-run.txt). The tests use simulated
desktop ports and OCR word fixtures. They verify the implemented rules, rather
than live OCR accuracy or persistence in Fakturama.

Annotations for the successful checks:

| Check | Evidence | Meaning |
| --- | --- | --- |
| Extraction | `test_extracts_sample_layout` | Parses the fixture into the expected Order, including addresses and two items. |
| Master data | `test_reuses_unique_matching_records` and `test_creates_missing_records_after_opening_order_and_reselects` | Exercises reuse and creation decisions through simulated ports. |
| Saved Order | `test_fills_source_order_and_verifies_saved_row` | Checks line values, totals, one save, and the simulated Documents row. |
| Paid Invoice | `test_paid_invoice_uses_follow_up_and_verifies_both_documents` | Checks follow-up from `ORD-100`, paid date `2026-07-18`, payment `678.30`, one save, and the original open Order. |
| Unpaid Invoice | `test_unpaid_invoice_has_no_payment_date_or_value` | Checks that no paid date or payment value is applied. |
| UI failure | `test_ambiguous_control_stops_with_screenshot` | Checks that ambiguity stops the action and attaches a screenshot path. The test writes placeholder bytes, not a real image. |
| Capture failure | `test_capture_failure_preserves_action_error_and_logs_reason` | Checks that capture errors are logged and the original UI failure survives. |

## Successful CLI fixture check

Calling `main(['fixture.png'])` with `read_image` patched to return the existing
`tests.test_extraction.sample_page()` fixture produced these actual logs:

```text
[INFO] Reading order image: fixture.png
[INFO] OCR completed: 91 words detected
[INFO] Extraction valid: 2 item(s), gross total 678.30
```

The returned exit code was `0`; parsed stdout JSON had `status: valid`, reference
`WEB-2026-0714-A17`, and gross total `678.30`. This check exercised the CLI,
parser, serialization, and progress logs with simulated OCR input.

## Failure check

Running the CLI with `missing-order.png` produced:

```text
[INFO] Reading order image: missing-order.png
[ERROR] Manual review required: Image not found: missing-order.png
{"status": "manual_review", "reason": "Image not found: missing-order.png"}
```

No desktop screenshot is expected for extraction failures because no UI action
has occurred. For UI failures, `FakturamaUI` captures the configured window and
includes the screenshot path in both the exception and error log.

## Initial Packaging Context

Tesseract was absent from `PATH` during initial packaging. It was later made
available, real-image extraction succeeded, and the desktop adapter was
implemented. The current live evidence and remaining acceptance check are
described at the top of this document; older fixture checks below are retained
as historical evidence, not substitutes for live persistence verification.

## Successful real-image extraction

The actual CLI run against `samples/order.png` exited with code `0`. Full logs
and extracted JSON are in [extraction-run.txt](extraction-run.txt).

Submission pair: [original order image](../samples/order.png) and
[standalone extracted JSON](../samples/order.extracted.json). The JSON preserves
the recorded CLI result exactly; packaging it did not rerun OCR or Fakturama.

Checked against the source image:

- Reference `WEB-2026-0714-A17`, Order date `2026-07-14`.
- Northstar Office GmbH, contact Marta Klein, alias and contact details.
- Billing `Friedrichstrasse 88, 10117 Berlin`; delivery `Beusselstrasse 44, 10553 Berlin`.
- Bank Transfer, `PAID`, payment date `2026-07-18`.
- Two items, quantities `2` and `3`, unit net prices `250.00` and `40.00`, discounts `10%` and `0%`, VAT `19%`.
- Net `570.00`, VAT `108.30`, gross `678.30`.

OCR preserves color and enlarges small print. The parser ignores isolated
table-border punctuation in the SKU column. Confidence threshold `70` and
line/total validation remain enforced. Recorded OCR in
`tests/fixtures/order_ocr.json` covers the real layout without requiring
Tesseract during unit tests. This successful extraction does not create any
Fakturama records.
