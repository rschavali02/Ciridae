import pytest
from app.extraction.fields import extract_fields

SAMPLE_TEXT = """
ACME Incorporated
Invoice #INV-1042
Date Due: 2026-09-15
PO Number: PO-88213

Line Items:
Consulting services - $4,500.00
Software license - $1,200.00

Total: $5,700.00
"""


@pytest.mark.integration
def test_extracts_fields_from_text():
    result = extract_fields(SAMPLE_TEXT)
    assert result.vendor_name == "ACME Incorporated"
    assert result.invoice_number == "INV-1042"
    assert result.amount == 5700.00
    assert result.po_number == "PO-88213"
    assert len(result.line_items) == 2
