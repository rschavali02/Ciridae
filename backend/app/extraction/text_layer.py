import pdfplumber


def extract_text_layer(pdf_path: str) -> str:
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()


def is_text_usable(text: str, min_length: int = 50, min_words: int = 10) -> bool:
    if len(text) < min_length:
        return False
    if len(text.split()) < min_words:
        return False
    return True
