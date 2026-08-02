import base64

from anthropic import Anthropic

from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)


def transcribe_via_vision(pdf_path: str) -> str:
    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64},
                },
                {
                    "type": "text",
                    "text": "Transcribe every piece of text visible in this document exactly as written, including handwritten notes. Output plain text only, no commentary.",
                },
            ],
        }],
    )
    text_block = next(block for block in response.content if block.type == "text")
    return text_block.text.strip()
