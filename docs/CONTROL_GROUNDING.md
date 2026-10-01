# Control Grounding Register

This file documents controls that could not be reached reliably through Microsoft UI Automation and therefore required visual grounding or OCR fallback.

## Scope

The adapter targets the inspected English Fakturama interface on Windows. Named controls, labels, native menus, checkboxes, and combo options are preferred. Numeric automation IDs in the supplied dumps are dynamic window handles, not stable selectors. No saved absolute screen coordinates are used.

Control activation uses the resolved native handle. Handleless SWT tab/tree items
use their current bounds relative to the resolved parent control. Custom table
cells use positions derived from the current capture. Draft closing uses the
native `File > Close` command, rather than a coordinate offset for the tab X.
Address searches are set and read back without sending Tab. Product searches
can automatically accept an exact SKU and close the selector; that path requires
exactly one new item row and native readback of the requested SKU. If the selector
stays open, the adapter requires the matching search text and a unique result.

## Custom SWT tables

Applies to Debtors, terms of payment, VATs, Products, Documents, address/product selectors, and Order/Invoice item grids.

- Semantic lookup attempted: UI Automation and native Windows inspection expose the table container, but not usable row/cell controls.
- Grounding: capture the current visible table, detect repeated body grid boundaries, and OCR its headers to identify columns. Derive row and cell click positions from that capture.
- Row detection: use a record column, including the noneditable Position column for item grids, so a selected inline editor does not hide a row.
- Editable item values: activate the grounded cell and read its native Edit control. After committing an edit, reopen the cell to verify the persisted value. Only an editor covering the targeted cell is accepted.
- Read-only values: OCR VAT, line totals, document identifiers, and State. Normalize selected/dark backgrounds and exclude grid/selection borders. VAT excludes the dropdown arrow; numeric confidence checks ignore nonnumeric decoration but reject uncertain or conflicting numeric values. The confidence threshold remains 70.
- State: exclude the leading status icon using an inset derived from row height, then read the label. This fixes the icon being misrecognized as uncertain text beside `open`.
- Verification: master identities are read from their native editors; item values, arithmetic, totals, saved document numbers, and transaction membership are checked after actions. Missing columns, ambiguity, or uncertain readback stops the run.

Retained submission captures illustrating these surfaces are [verified-documents.png](../evidence/live-run/verified-documents.png) and [saved-invoice.png](../evidence/live-run/saved-invoice.png). Redundant inspection and debug screenshots were removed.

## Inspected Unlabeled Controls

| Screen / control | Grounding and reason | Verification |
| --- | --- | --- |
| Order: address catalogue icon | UI Automation exposes unnamed Images. Use the first Image in the pane anchored by the exact `Addresses` label; its function was established by visual inspection. | Require the named `Select the address` dialog, unique search result, debtor identity, and exact structured addresses. |
| Order: product catalogue icon | Use the first Image in the pane anchored by the exact `Items` label; the unnamed catalogue icon was visually inspected. | Require `Select a product`, a unique visible search field/result, final item count, and exact SKU/value readback. |
| Contact: address-role popup | The popup opener is an unlabeled button following the `address type` label in its inspected sibling pane. | Use named `Invoice address` and `Delivery address` checkboxes and verify their toggle states. |
| Main product toolbar action | `Create a new product` is named but duplicated in the Products view. Select the unique visible action positioned above the active editor. The installed native New-product menu produced an application error during inspection. | Require the product editor's `Item Number` label, then verify and re-find the saved product. |

Address tabs, the Order's `Create a follow-up document > Invoice` button, the `paid` checkbox, and the Documents `This transaction` tree item are named controls. `This transaction` requires double-click activation after search is cleared; transaction document identifiers are then verified from the grounded table.

## Failure Evidence and Limits

The retained failure capture is [desktop_1790880781276177700.png](../evidence/live-run/desktop_1790880781276177700.png), paired with the unsaved-editor stop reason in `evidence/live-run/run.log`. Its short caption is in [ANNOTATED_RUN.md](../evidence/ANNOTATED_RUN.md). Other historical failure screenshots were removed as redundant; no failure was repeated for this cleanup.

Different application versions, display layouts, themes, or languages may change these relationships. The adapter stops on missing/ambiguous controls or unreadable geometry rather than guessing. The pending uninterrupted acceptance run is documented separately from the already verified staged run.
