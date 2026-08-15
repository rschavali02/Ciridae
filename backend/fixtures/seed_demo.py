"""Reset the database to a clean, demo-ready state and seed the Stark
Industries and Acme Inc scenarios.

Run: python -m fixtures.seed_demo

Wipes every data-bearing table except `documents` (the policy corpus --
re-embedding it costs real Voyage calls for no reason before a demo) and
reseeds:

- The standing vendor master: ACME Incorporated, Globex Corp, Stark
  Industries -- all `active`, so lookup_vendor resolves cleanly and
  draft_vendor is never triggered for any of them.
- Three past *approved* invoices each for Stark Industries and ACME
  Incorporated, bracketing the demo invoices' amounts. Without these,
  get_invoice_history returns count=0 for what reads as a first-time payee,
  and the agent has been observed treating that as a caution worth a lower
  confidence -- a real risk in an unscripted live run with no second take.
  None of the seeded amounts equal a demo invoice's amount or each other's
  invoice numbers: check_duplicate_invoice matches on amount OR invoice
  number, so a collision would misfire as a duplicate finding.
- Two purchase orders, both sized so §II decides the outcome arithmetically
  rather than by model judgment. PO-88213 at $5,000 puts the seeded Acme
  invoice's $5,700 outside the tolerance; PO-77401 at $9,500 puts the live
  Stark upload's $9,780.10 inside it. One clause, two outcomes -- see the
  ACME_PO and STARK_PO comments below for the arithmetic.
- Two already-decided Acme Inc invoices -- one approved, one escalated --
  each a complete Invoice + AgentRun pair with a hand-built transcript using
  the exact tool-output shapes `app/agent/tools.py` produces, so they render
  in the dashboard identically to a live run without needing one. Their
  `Invoice.status` stays "pending": the agent has decided, but no human has
  acted, which is exactly the state a real reviewer would find them in.

`invoice_05.pdf` (Stark Industries) is NOT seeded as a row here -- it is
uploaded live during the demo. Seeding it would mean demoing an invoice that
already has a decision. Its PO *is* seeded above, because the agent can only
reach `get_purchase_order` if the number it reads off the document resolves
to something. The document was regenerated to print "PO-77401" for exactly
that reason: the earlier version referenced no PO, so §III's unanswerable
"was a PO required at this amount?" question was the only PO-shaped thing
the agent could reason about, and it escalated -- correctly, and unhelpfully
for a demo.
"""

import asyncio
from datetime import date, datetime, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.models import AgentRun, Invoice, LineItem, PurchaseOrder, Vendor

# Same order used by the eval harness's per-trial reset: children before the
# tables they reference. `documents` (the policy corpus) is deliberately
# excluded -- it costs real embedding calls to rebuild and nothing here
# touches it.
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

# Brackets the $9,780.10 demo invoice (INV-1004) without matching it or each
# other -- see the module docstring for why an exact match would misfire.
STARK_HISTORY = [
    {"invoice_number": "INV-0901", "amount": 9200.00},
    {"invoice_number": "INV-0930", "amount": 9650.00},
    {"invoice_number": "INV-0965", "amount": 10100.00},
]

# Backs get_invoice_history/check_duplicate_invoice for both seeded Acme
# invoices below (invoice_01.pdf's $2,805.84, clean_acme.pdf's $5,700.00) --
# neither amount nor invoice number collides with this list.
ACME_HISTORY = [
    {"invoice_number": "INV-0501", "amount": 2600.00},
    {"invoice_number": "INV-0530", "amount": 4200.00},
    {"invoice_number": "INV-0560", "amount": 5900.00},
]

# clean_acme.pdf prints "PO-88213" and totals $5,700.00. Seeding the PO at
# $5,000 makes the escalation deterministic policy math, not a model judgment
# call: variance = |5700 - 5000| = $700 (14.0%); §II caps the allowable
# variance at the LESSER of 10% of the PO ($500) or $1,000 -- so $700 exceeds
# the cap on both the percentage and dollar basis.
ACME_PO = {"po_number": "PO-88213", "amount": 5000.00, "currency": "USD"}

# The other side of the §II demo: the same clause, applied to an invoice that
# passes it. invoice_05.pdf prints "PO-77401" and totals $9,780.10, so the
# variance is $280.10 (2.95%) against the LESSER of 10% of the PO ($950) or
# $1,000 -- inside the cap on both bases, so it should approve *because* the
# agent read the rule, not because no rule applied.
#
# That distinction is the point of seeding this at all. Every other approval in
# the demo is a no-PO invoice where §II simply does not bite, which leaves
# retrieval looking like a brake that only ever blocks. This is the one beat
# where a retrieved clause is what lets a payment through.
#
# Sized well clear of the cap rather than just under it. A variance a few
# dollars inside $950 would make a correct approval indistinguishable from a
# rounding error in front of an audience, and would turn any drift in the
# extracted total into a live coin flip.
STARK_PO = {"po_number": "PO-77401", "amount": 9500.00, "currency": "USD"}

# The real §II clause, retrieved verbatim from the live `documents` table
# rather than invented, so the seeded search_policy tool call and the
# escalation reasoning below are consistent with what the policy actually
# says if anyone inspects the tool-call timeline.
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

        # Both seeded Acme invoices share the same vendor resolution and
        # payment-history findings -- only the PO check and final decision
        # differ. Computed once here rather than duplicated per-transcript.
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
