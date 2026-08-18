"""Response shapes for the API.

Declared once here and handed to FastAPI as `response_model`, so the OpenAPI
schema is complete and the frontend's TypeScript types can be generated from it
rather than maintained by hand.
"""

from typing import Any

from pydantic import BaseModel


class Health(BaseModel):
    status: str


class InvoiceAccepted(BaseModel):
    """The 202 from an upload: enough to start polling with."""

    id: str
    status: str


class ToolCall(BaseModel):
    tool: str
    input: dict[str, Any]
    output: Any


class PolicyClause(BaseModel):
    section: str | None
    text: str | None


class InvoiceSummary(BaseModel):
    """One queue row."""

    id: str
    invoice_number: str | None
    vendor_name: str | None
    amount: float | None
    currency: str | None
    status: str
    created_at: str | None
    run_status: str | None
    decision: str | None
    confidence: float | None
    reasoning: str | None


class InvoiceDetail(InvoiceSummary):
    """A queue row plus the transcript of how it was decided."""

    due_date: str | None
    po_number: str | None
    tool_calls: list[ToolCall]
    policy_clauses: list[PolicyClause]


class ActivityLatest(BaseModel):
    tool: str | None
    input: dict[str, Any] | None


class Activity(BaseModel):
    """What the agent is doing right now. Polled about once a second."""

    status: str | None
    latest: ActivityLatest | None
    call_count: int
    decision: str | None


class AuditEntry(BaseModel):
    id: str
    invoice_id: str
    vendor_name: str | None
    amount: float | None
    currency: str | None
    action: str
    note: str | None
    agent_decision: str | None
    decided_at: str | None


class PendingVendor(BaseModel):
    id: str
    name: str
    bank_details: str | None


class VendorApproved(BaseModel):
    """The result of a human approving a drafted payee."""

    id: str
    name: str
    approval_status: str
    invoices_linked: int
