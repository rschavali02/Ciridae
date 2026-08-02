import pytest
from app.extraction.pipeline import extract_invoice


@pytest.mark.integration
def test_pipeline_uses_text_layer_for_clean_pdf():
    result = extract_invoice("fixtures/invoices/clean_acme.pdf")
    assert result.fields.vendor_name is not None
    assert result.used_vision_fallback is False


@pytest.mark.integration
def test_pipeline_falls_back_to_vision_for_scanned_pdf():
    result = extract_invoice("fixtures/invoices/messy_scanned.pdf")
    assert result.used_vision_fallback is True
    assert result.fields.vendor_name is not None
