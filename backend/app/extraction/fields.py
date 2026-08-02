from pydantic import BaseModel

from anthropic import Anthropic

from app.config import settings

client = Anthropic(api_key=settings.anthropic_api_key)


class LineItemFields(BaseModel):
    description: str
    amount: float


class ExtractedFields(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    amount: float | None = None
    due_date: str | None = None  # ISO 8601, agent/caller parses to date
    po_number: str | None = None
    line_items: list[LineItemFields] = []


EXTRACT_TOOL = {
    "name": "record_extracted_fields",
    "description": "Record the structured fields extracted from an invoice's text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor_name": {"type": ["string", "null"]},
            "invoice_number": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]},
            "due_date": {"type": ["string", "null"], "description": "ISO 8601 date"},
            "po_number": {"type": ["string", "null"]},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "amount": {"type": "number"},
                    },
                    "required": ["description", "amount"],
                },
            },
        },
        "required": ["vendor_name", "amount", "line_items"],
    },
}


def extract_fields(raw_text: str) -> ExtractedFields:
    response = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=1024,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_extracted_fields"},
        messages=[{
            "role": "user",
            "content": f"Extract the invoice fields from this text:\n\n{raw_text}",
        }],
    )
    tool_call = next(b for b in response.content if b.type == "tool_use")
    return ExtractedFields(**tool_call.input)
