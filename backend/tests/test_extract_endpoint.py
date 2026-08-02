import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.mark.integration
def test_extract_endpoint_returns_fields_for_clean_pdf():
    client = TestClient(app)
    with open("fixtures/invoices/clean_acme.pdf", "rb") as f:
        response = client.post("/extract", files={"file": ("clean_acme.pdf", f, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert body["fields"]["vendor_name"] == "Acme Inc"
    assert body["used_vision_fallback"] is False
    assert len(body["fields"]["line_items"]) == 2


@pytest.mark.integration
def test_extract_endpoint_flags_vision_fallback_for_scanned_pdf():
    client = TestClient(app)
    with open("fixtures/invoices/messy_scanned.pdf", "rb") as f:
        response = client.post("/extract", files={"file": ("messy_scanned.pdf", f, "application/pdf")})
    assert response.status_code == 200
    body = response.json()
    assert body["used_vision_fallback"] is True
