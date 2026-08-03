"""Dev utility: run the agent end-to-end on one real invoice and print the trace.

Not part of the app. Seeds a vendor, a PO and an invoice, runs the agent, then
prints every tool call and the final decision so the transcript can be read by
eye -- the "check the transcripts" step, before any eval harness exists.

Usage:
    PYTHONPATH=. python scripts/try_agent.py
"""
import asyncio
import json

from sqlalchemy import text

from app.agent.runner import run_agent
from app.db import SessionLocal
from app.extraction.pipeline import extract_invoice
from app.models import Invoice, PurchaseOrder, Vendor

PDF = "fixtures/invoices/clean_acme.pdf"


async def main() -> None:
    result = extract_invoice(PDF)
    fields = result.fields
    print(f"extracted: vendor={fields.vendor_name!r} amount={fields.amount} po={fields.po_number}")

    async with SessionLocal() as session:
        # Clean slate so repeat runs do not accumulate history and quietly
        # change what the agent sees between runs.
        for table in ("agent_runs", "audit_log", "line_items", "invoices", "purchase_orders", "vendors"):
            await session.execute(text(f"DELETE FROM {table}"))

        vendor = Vendor(
            name="ACME Incorporated",
            normalized_name="acme incorporated",
            bank_details="IBAN GB00ACME00000000000001",
        )
        session.add(vendor)
        session.add(PurchaseOrder(po_number=fields.po_number or "PO-88213", amount=fields.amount))
        await session.flush()

        # Three prior payments, so get_invoice_history has something to say.
        for amount in (5400.0, 5600.0, 5900.0):
            session.add(
                Invoice(
                    vendor_id=vendor.id,
                    amount=amount,
                    status="approved",
                    raw_pdf_path="historical.pdf",
                )
            )

        invoice = Invoice(
            vendor_id=vendor.id,
            invoice_number=fields.invoice_number,
            amount=fields.amount,
            po_number=fields.po_number,
            raw_text=result.raw_text,
            raw_pdf_path=PDF,
            status="pending",
        )
        session.add(invoice)
        await session.commit()
        await session.refresh(invoice)

        transcript = await run_agent(session, invoice)

    print("\n--- tool calls ---")
    for call in transcript.tool_calls:
        print(f"\n  {call['tool']}({json.dumps(call['input'])})")
        print(f"    -> {json.dumps(call['output'])}")

    print("\n--- decision ---")
    print(f"  {transcript.decision}  (confidence {transcript.confidence})")
    print(f"  {transcript.reasoning}")


if __name__ == "__main__":
    asyncio.run(main())
