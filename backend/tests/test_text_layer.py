from app.extraction.text_layer import extract_text_layer, is_text_usable


def test_extracts_text_from_clean_pdf():
    text = extract_text_layer("fixtures/invoices/clean_acme.pdf")
    assert len(text) > 50
    assert is_text_usable(text) is True


def test_flags_scanned_pdf_as_unusable():
    text = extract_text_layer("fixtures/invoices/messy_scanned.pdf")
    assert is_text_usable(text) is False


def test_usability_threshold_on_empty_text():
    assert is_text_usable("") is False
    assert is_text_usable("a b") is False
