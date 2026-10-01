# Submission Notes

## What is included

This repository contains image extraction and validation, workflow rules, and a Windows adapter implementing Fakturama master-data resolution, Order completion, linked Invoice creation, payment, and saved-document verification.

The separate design document and the written answer to "If you had 3 more hours"
are completed and supplied separately by the author; they are not bundled here.

Extraction-only command, with no desktop changes:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation samples/order.png --log-level INFO
```

Desktop command, for a running English Fakturama instance in a separate, empty test workspace:

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation samples/order.png --run --log-level INFO --evidence-dir evidence/acceptance-run
```

The CLI returns JSON on stdout and logs on stderr. Successful extraction returns `status: valid`; desktop completion returns `status: completed` with document numbers and total. Both exit with code `0`. Review conditions exit with code `2` and return `status: manual_review`.

Unsaved editors and an existing source reference block a new run. Saves are not retried automatically. Review saved records and drafts after an interrupted run before deciding how to continue.

## Dependencies

- Python 3.10 or newer
- Tesseract OCR with English language data on `PATH`
- `Pillow>=10,<12`
- `pywinauto>=0.6.8,<0.7` on Windows
- running Fakturama in an interactive Windows session for desktop actions

Install Python packages with:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use the repository's `.venv` interpreter, not a global Python lacking these packages. Tesseract must be on `PATH` with `eng` language data. The inspected sample setup uses EUR and Germany's currency locale in Fakturama's preferences. Full setup details are in [README.md](../README.md).

## Logging and screenshots

The CLI logs image extraction, workflow stages, menu actions, document identifiers, and review reasons. Desktop-stage logs are appended to `run.log` under `--evidence-dir`; earlier extraction messages go to stderr.

```powershell
.\.venv\Scripts\python.exe -m fakturama_automation samples/order.png --log-level INFO
```

UI failures attempt timestamped screenshots in the evidence directory. Other desktop exceptions attempt `manual-review.png`. Successful runs also capture saved/verified document views. Capture errors are logged without masking an action failure or changing the outcome of a completed save.

## Current evidence

Annotated evidence for the implemented portion is in [evidence/ANNOTATED_RUN.md](../evidence/ANNOTATED_RUN.md).

Existing live evidence includes saved Order `PO000002`, linked Invoice `INV000001`, their transaction view, and actual failure examples. Final readback verified EUR 678.30, Bank Transfer, Paid, payment date July 18, 2026, and the original Order remaining Open. Creation was completed in stages during debugging; this is not evidence of one uninterrupted CLI run.

The latest executed suite passed 52 tests. Unit tests use fixtures and test doubles; their placeholder screenshot bytes are not presented as real evidence. The older `evidence/test-run.txt` records 34 tests only.

The latest acceptance attempt reached line/totals validation and a save request
for Order `PO000005`, then stopped because save completion could not be confirmed.
It did not create the linked Invoice. The save must be treated as uncertain.
The [acceptance log](../evidence/acceptance-run/run.log) and
[save-timeout screenshot](../evidence/acceptance-run/desktop_1790886478804934700.png)
are retained. Earlier debug captures and control dumps were removed; older log
entries may refer to screenshots that are no longer included.

## Visual grounding

Custom SWT tables do not expose usable cells through UI Automation. Their current grid geometry and headers are visually grounded, with native inline editors used where possible and confidence-gated OCR for read-only values. Inspected unlabeled icons and the product toolbar's placement are also recorded in [CONTROL_GROUNDING.md](CONTROL_GROUNDING.md).

## Known limitations

- A fresh, uninterrupted CLI run in an empty test workspace remains outstanding. Any failure discovered there must be fixed before claiming full unattended acceptance.
- Product existence checks currently use `Data > Products` before the Order selector, rather than using the selector as the first existence check. Debtor search starts in the Order selector, but exact identity is read from the Debtors editor rather than the selector's visible columns.
- Newly created Debtors are saved before payment-method resolution; the adapter does not set the Debtor's Payment tab before saving. Invoice payment is handled separately.
- For separate delivery addresses, the adapter can replace the Order delivery block from a matching cached debtor address before checking it, rather than only confirming the automatically populated block.
- The parser targets the supplied Sales Order Input image layout.
- The adapter was inspected against the installed English Fakturama interface; other versions, languages, themes, and layouts have not been verified.
- Tables must be visible and readable. Ambiguous or uncertain data stops for manual review rather than being guessed.
- The CLI connects to Fakturama; it does not install or launch it.
- The latest cleanup reran the 52-test unit suite without launching or interacting with Fakturama.
