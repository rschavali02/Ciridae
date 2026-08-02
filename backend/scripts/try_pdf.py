"""Dev utility: run the extraction pipeline on any PDF and print the result.

Not part of the app -- for manually spot-checking new/real-world invoice PDFs
that don't have hand-verified ground truth (unlike backend/fixtures/invoices/).

Usage:
    python scripts/try_pdf.py path/to/some_invoice.pdf [more.pdf ...]
"""
import sys

from app.extraction.pipeline import extract_invoice

for path in sys.argv[1:]:
    print(f"\n=== {path} ===")
    try:
        result = extract_invoice(path)
    except Exception as exc:
        print(f"  FAILED: {exc}")
        continue
    print(f"  vision fallback used: {result.used_vision_fallback}")
    print(f"  fields: {result.fields}")
