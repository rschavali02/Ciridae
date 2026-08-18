"""Reset the database to a demo-ready state.

Run: python -m fixtures.seed_demo

Wipes every table except the policy corpus, then seeds the vendor master,
payment history, two purchase orders, and two already-decided Acme invoices.
Stark's invoice_05.pdf is left out on purpose -- it is uploaded live in the demo.
"""

import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.models import AgentRun, Invoice, LineItem, PurchaseOrder, Vendor

# Children first. `documents` is excluded: re-embedding it costs real API calls.
_TABLES = ("agent_runs", "audit_log", "line_items", "invoices", "purchase_orders", "vendors")

VENDORS = [
    {
        "name": "ACME Incorporated",
        "normalized_name": "acme incorporated",
        "bank_details": "IBAN GB00ACME00000000000001",
        "approval_status": "active",
    },
    {
        "name": "Globex Corp",
        "normalized_name": "globex corp",
        "bank_details": "IBAN GB00GLBX00000000000002",
        "approval_status": "active",
    },
    {
        "name": "Stark Industries",
        "normalized_name": "stark industries",
        "bank_details": "IBAN GB00STRK00000000000003",
        "approval_status": "active",
    },
]

# Brackets the $9,780.10 demo invoice without matching its amount or number.
STARK_HISTORY = [
    {"invoice_number": "INV-0901", "amount": 9200.00},
    {"invoice_number": "INV-0930", "amount": 9650.00},
    {"invoice_number": "INV-0965", "amount": 10100.00},
]

# Brackets both seeded Acme invoices without colliding with either.
ACME_HISTORY = [
    {"invoice_number": "INV-0501", "amount": 2600.00},
    {"invoice_number": "INV-0530", "amount": 4200.00},
    {"invoice_number": "INV-0560", "amount": 5900.00},
]

# $5,700 invoice against a $5,000 PO: $700 over, outside §II's cap. Escalates.
ACME_PO = {"po_number": "PO-88213", "amount": 5000.00, "currency": "USD"}

# $9,780.10 invoice against a $9,500 PO: $280 over, well inside §II's cap.
STARK_PO = {"po_number": "PO-77401", "amount": 9500.00, "currency": "USD"}

# Verbatim §II, so the seeded transcripts cite what the policy actually says.
POLICY_II_TEXT = (
    "This policy identifies control actions to mitigate potential risks related to "
    "accounts payable and establishes the following:  All invoices must be verified "
    "to ensure payments are appropriately made to the correct vendor for the correct "
    "amount for goods and services delivered.  For purchase order based payments, "
    "discrepancies between the vendor invoice and the purchase order greater than 10 "
    "percent or $1,000 USD or equivalent in local currency (the lesser of the two) "
    "must be resolved before the payment can be processed.  The Payment Request "
    "Checklist must be completed for all payments.  The Signature Control document "
    "is the responsibility of each office to maintain based on the template and "
    "instructions provided in this policy and must be relied upon to confirm "
    "authorized approvers when processing payments.  There must be an appropriate "
    "segregation of functional responsibilities to ensure appropriate financial "
    "controls from the initiation of a financial commitment up to its actual payment."
)


async def seed() -> None:
    async with SessionLocal() as session:
        for table in _TABLES:
            await session.execute(text(f"DELETE FROM {table}"))

        vendors = [Vendor(**v) for v in VENDORS]
        session.add_all(vendors)
        await session.flush()

        stark = next(v for v in vendors if v.name == "Stark Industries")
        acme = next(v for v in vendors if v.name == "ACME Incorporated")

        for vendor, history in ((stark, STARK_HISTORY), (acme, ACME_HISTORY)):
            for past in history:
                session.add(
                    Invoice(
                        vendor_id=vendor.id,
                        currency="USD",
                        status="approved",
                        raw_pdf_path="fixtures/invoices/historical.pdf",
                        **past,
                    )
                )

        session.add_all([PurchaseOrder(**ACME_PO), PurchaseOrder(**STARK_PO)])
        await session.flush()

        # Shared by both transcripts; only the PO check and decision differ.
        acme_id = str(acme.id)
        history_since = datetime.now(timezone.utc).isoformat()

        lookup_vendor_output = {
            "match": "resolved",
            "vendor_id": acme_id,
            "vendor_name": acme.name,
            "bank_details": acme.bank_details,
        }
        invoice_history_output = {
            "count": len(ACME_HISTORY),
            "lookback_days": 365,
            "average_amount": round(sum(h["amount"] for h in ACME_HISTORY) / len(ACME_HISTORY), 2),
            "min_amount": min(h["amount"] for h in ACME_HISTORY),
            "max_amount": max(h["amount"] for h in ACME_HISTORY),
            "most_recent_date": history_since,
        }
        policy_clause_output = {"clauses": [{"section": "II. Policy", "text": POLICY_II_TEXT}]}

        # -- Approved: invoice_01.pdf, "Acme Inc", INV-1000, $2,805.84, no PO --
        approve_reasoning = (
            "This invoice from ACME Incorporated is a routine, low-risk payment that "
            "matches the vendor's established pattern, so I'm recommending approval.\n\n"
            "Vendor: lookup_vendor resolved the printed name \"Acme Inc\" to the active "
            "vendor record ACME Incorporated, with bank details on file, so payment "
            "routes to a verified payee.\n\n"
            "Payment history: get_invoice_history found 3 approved payments to this "
            "vendor in the last year, ranging from $2,600.00 to $5,900.00 with an "
            "average of $4,233.33. This invoice's $2,805.84 falls within that range.\n\n"
            "Duplicate check: check_duplicate_invoice found no prior payment matching "
            "this invoice number or amount.\n\n"
            "Purchase order: no PO is referenced on this invoice, and the line items "
            "(web development and software licensing) read as ordinary recurring "
            "services rather than a purchase that would typically require one.\n\n"
            "Policy: search_policy confirmed the only applicable requirement -- "
            "verifying the correct vendor and amount -- is satisfied; the PO-variance "
            "controls in §II do not apply since no PO is referenced."
        )
        approve_submit_output = {
            "original_decision": "approve",
            "final_decision": "approve",
            "overridden": False,
            "override_reason": None,
            "confidence": 0.93,
            "reasoning": approve_reasoning,
        }
        approve_invoice = Invoice(
            vendor_id=acme.id,
            invoice_number="INV-1000",
            amount=2805.84,
            currency="USD",
            due_date=date(2026, 3, 28),
            po_number=None,
            status="pending",
            confidence_score=0.93,
            raw_pdf_path="fixtures/invoices/invoice_01.pdf",
        )
        session.add(approve_invoice)
        await session.flush()
        session.add_all(
            [
                LineItem(invoice_id=approve_invoice.id, description="Web development (9 x $102.83)", amount=925.47),
                LineItem(invoice_id=approve_invoice.id, description="Software license (9 x $208.93)", amount=1880.37),
            ]
        )
        session.add(
            AgentRun(
                invoice_id=approve_invoice.id,
                source="live",
                status="complete",
                decision="approve",
                confidence=0.93,
                transcript={
                    "tool_calls": [
                        {
                            "tool": "lookup_vendor",
                            "input": {"vendor_name": "Acme Inc"},
                            "output": lookup_vendor_output,
                        },
                        {
                            "tool": "get_invoice_history",
                            "input": {"vendor_id": acme_id},
                            "output": invoice_history_output,
                        },
                        {
                            "tool": "check_duplicate_invoice",
                            "input": {"vendor_id": acme_id, "amount": 2805.84, "invoice_number": "INV-1000"},
                            "output": {"match": "none", "detail": "No prior payment to this vendor resembles it."},
                        },
                        {
                            "tool": "search_policy",
                            "input": {"query": "invoice approval requirements no purchase order"},
                            "output": policy_clause_output,
                        },
                        {
                            "tool": "submit_recommendation",
                            "input": {"decision": "approve", "confidence": 0.93, "reasoning": approve_reasoning},
                            "output": approve_submit_output,
                        },
                    ],
                    "reasoning": approve_reasoning,
                },
            )
        )

        # -- Escalated: clean_acme.pdf, "Acme Inc", INV-2001, $5,700.00, PO-88213 --
        escalate_reasoning = (
            "This invoice from ACME Incorporated cannot be approved automatically "
            "because it breaches the purchase order variance tolerance in §II of "
            "the AP policy, so I'm escalating it for human review.\n\n"
            "Vendor: lookup_vendor resolved the printed name \"Acme Inc\" to the active "
            "vendor record ACME Incorporated, with bank details on file -- the payee "
            "itself is not in question.\n\n"
            "Payment history: get_invoice_history found 3 approved payments to this "
            "vendor in the last year, ranging from $2,600.00 to $5,900.00 with an "
            "average of $4,233.33. This invoice's $5,700.00 is within that historical "
            "range, so the amount alone is not the concern.\n\n"
            "Duplicate check: check_duplicate_invoice found no prior payment matching "
            "this invoice number or amount.\n\n"
            "Purchase order: get_purchase_order found PO-88213 on file for $5,000.00 "
            "in USD, matching the invoice's currency. The invoice totals $5,700.00, a "
            "variance of $700.00 (14.0%) over the PO amount.\n\n"
            "Policy: search_policy returned §II, which caps PO variance at the "
            "lesser of 10% or $1,000 USD before a discrepancy must be resolved. 10% of "
            "this PO is $500.00, so the $700.00 variance exceeds the cap on both the "
            "percentage and dollar basis -- the discrepancy must be resolved before "
            "payment, which requires a person, not this agent."
        )
        escalate_submit_output = {
            "original_decision": "escalate",
            "final_decision": "escalate",
            "overridden": False,
            "override_reason": None,
            "confidence": 0.88,
            "reasoning": escalate_reasoning,
        }
        escalate_invoice = Invoice(
            vendor_id=acme.id,
            invoice_number="INV-2001",
            amount=5700.00,
            currency="USD",
            due_date=date(2026, 9, 15),
            po_number="PO-88213",
            status="pending",
            confidence_score=0.88,
            raw_pdf_path="fixtures/invoices/clean_acme.pdf",
        )
        session.add(escalate_invoice)
        await session.flush()
        session.add_all(
            [
                LineItem(invoice_id=escalate_invoice.id, description="Consulting services", amount=4500.00),
                LineItem(invoice_id=escalate_invoice.id, description="Software license", amount=1200.00),
            ]
        )
        session.add(
            AgentRun(
                invoice_id=escalate_invoice.id,
                source="live",
                status="complete",
                decision="escalate",
                confidence=0.88,
                transcript={
                    "tool_calls": [
                        {
                            "tool": "lookup_vendor",
                            "input": {"vendor_name": "Acme Inc"},
                            "output": lookup_vendor_output,
                        },
                        {
                            "tool": "get_invoice_history",
                            "input": {"vendor_id": acme_id},
                            "output": invoice_history_output,
                        },
                        {
                            "tool": "check_duplicate_invoice",
                            "input": {"vendor_id": acme_id, "amount": 5700.00, "invoice_number": "INV-2001"},
                            "output": {"match": "none", "detail": "No prior payment to this vendor resembles it."},
                        },
                        {
                            "tool": "get_purchase_order",
                            "input": {"po_number": "PO-88213", "invoice_amount": 5700.00, "invoice_currency": "USD"},
                            "output": {
                                "exists": True,
                                "po_number": "PO-88213",
                                "po_amount": 5000.00,
                                "po_currency": "USD",
                                "invoice_currency": "USD",
                                "currency_match": True,
                                "invoice_amount": 5700.00,
                                "variance_amount": 700.00,
                                "variance_percent": 14.0,
                            },
                        },
                        {
                            "tool": "search_policy",
                            "input": {"query": "purchase order variance tolerance discrepancy"},
                            "output": policy_clause_output,
                        },
                        {
                            "tool": "submit_recommendation",
                            "input": {"decision": "escalate", "confidence": 0.88, "reasoning": escalate_reasoning},
                            "output": escalate_submit_output,
                        },
                    ],
                    "reasoning": escalate_reasoning,
                },
            )
        )

        await session.commit()

    print(f"reset {len(_TABLES)} tables")
    print(f"seeded {len(VENDORS)} vendors: {', '.join(v['name'] for v in VENDORS)}")
    print(f"seeded {len(STARK_HISTORY)} approved past invoices for Stark Industries")
    print(f"seeded {len(ACME_HISTORY)} approved past invoices for ACME Incorporated")
    print(f"seeded purchase order {ACME_PO['po_number']} at ${ACME_PO['amount']:.2f}")
    print(f"seeded purchase order {STARK_PO['po_number']} at ${STARK_PO['amount']:.2f}")
    print("seeded 2 completed Acme Inc invoices: INV-1000 (approved), INV-2001 (escalated)")


if __name__ == "__main__":
    asyncio.run(seed())
