import pytest
from app.extraction.vision_fallback import transcribe_via_vision


@pytest.mark.integration
def test_transcribes_scanned_invoice():
    text = transcribe_via_vision("fixtures/invoices/messy_scanned.pdf")
    assert len(text) > 50
    # loose check -- exact wording from OCR/vision varies
    assert any(word in text.lower() for word in ["invoice", "total", "amount", "due"])
