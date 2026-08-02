# Fixture invoice ground truth

Reference data for verifying extraction results (Phase 1) and writing eval cases (Task 27).

This file covers `clean_acme.pdf`, `clean_globex.pdf`, and `messy_scanned.pdf` specifically —
these three are the regression set, referenced by exact filename in test code
(`test_text_layer.py`, `test_vision_fallback.py`, `test_pipeline.py`, `test_extract_endpoint.py`).
Don't rename or delete them without updating those tests.

`invoice_01.pdf` through `invoice_20.pdf` are a separate, generated volume set (not referenced by
any test) for manual spot-checking via `scripts/try_pdf.py` — their ground truth is in
`GROUND_TRUTH.json` alongside this file.

## clean_acme.pdf
- Vendor (as printed): Acme Inc
- Invoice #: INV-2001
- Due date: 2026-09-15
- PO number: PO-88213
- Line items: Consulting services $4,500.00; Software license $1,200.00
- Total: $5,700.00

## clean_globex.pdf
- Vendor (as printed): Globex Corp
- Invoice #: INV-3050
- Due date: 2026-08-30
- PO number: none
- Line items: Office supplies $320.00; Shipping $45.00
- Total: $365.00

## messy_scanned.pdf
- No embedded text layer (pure image) — exercises the vision fallback
- Vendor (as printed): ACME Incorporated
- Invoice #: INV-9981
- Due date: 2026-10-01
- PO number: PO-77102
- Line items: Emergency repair services $2,150.00; Parts and materials $640.00
- Total: $2,790.00
- Handwritten note: "Approved - pay ASAP -J.R."
