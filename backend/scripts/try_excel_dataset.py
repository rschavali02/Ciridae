"""Dev utility: run extract_fields() over the converted_invoice_dataset.xlsx
rows and print our output next to the dataset's ground truth for spot-checking.

Not part of the app. Skips the PDF/vision layer entirely -- the "Input" column
is already-OCR'd text, so this only exercises app.extraction.fields.extract_fields.

Needs openpyxl (dev-only, not in requirements.txt): pip install openpyxl

Usage:
    python scripts/try_excel_dataset.py [--limit N]
"""
import argparse
import json

import openpyxl

from app.extraction.fields import extract_fields

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=None)
args = parser.parse_args()

wb = openpyxl.load_workbook("converted_invoice_dataset.xlsx", data_only=True)
ws = wb["Sheet1"]
rows = list(ws.iter_rows(values_only=True))[1:]  # skip header
if args.limit:
    rows = rows[: args.limit]

hits = {"amount": 0, "invoice_number": 0}
total = 0

for i, (input_text, ground_truth_json) in enumerate(rows):
    ground_truth = json.loads(ground_truth_json)
    try:
        result = extract_fields(input_text)
    except Exception as exc:
        print(f"[{i}] FAILED: {exc}")
        continue

    total += 1
    gt_amount = ground_truth.get("TOTAL_AMOUNT") or ground_truth.get("GRAND_TOTAL") or ground_truth.get("TOTALS")
    gt_invoice_number = ground_truth.get("INVOICE_NUMBER")

    print(f"[{i}] amount={result.amount!r} vs ground_truth={gt_amount!r} | "
          f"invoice_number={result.invoice_number!r} vs ground_truth={gt_invoice_number!r}")

print(f"\nProcessed {total} rows. Review mismatches above by eye -- "
      f"ground-truth field names don't map 1:1 onto ours, so this is a spot-check, not a scored eval.")
