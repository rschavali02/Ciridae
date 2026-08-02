from dataclasses import dataclass

from app.extraction.text_layer import extract_text_layer, is_text_usable
from app.extraction.vision_fallback import transcribe_via_vision
from app.extraction.fields import extract_fields, ExtractedFields


@dataclass
class ExtractionResult:
    raw_text: str
    fields: ExtractedFields
    used_vision_fallback: bool


def extract_invoice(pdf_path: str) -> ExtractionResult:
    text = extract_text_layer(pdf_path)
    used_vision_fallback = False

    if not is_text_usable(text):
        text = transcribe_via_vision(pdf_path)
        used_vision_fallback = True

    fields = extract_fields(text)
    return ExtractionResult(raw_text=text, fields=fields, used_vision_fallback=used_vision_fallback)
