"""The twelve cases the agent is scored against.

Two rules govern every case here.

**A case may only test a rule the policy actually states.** The clause behind
most of them is §II: "discrepancies between the vendor invoice and the purchase
order greater than 10 percent or $1,000 USD or equivalent in local currency (the
lesser of the two) must be resolved before the payment can be processed." A case
asserting a rule the corpus does not contain is a broken task, and it will read
as an agent failure forever without ever being one.

**Every anomaly is paired with a near-identical case that should pass.** Cases
6, 7 and 8 differ only in how far the invoice diverges from its PO; 2 and 3
differ only in whether the invoice number matches exactly. Drop the pairs and an
agent that escalates everything scores well, which is the one-sided
optimization the suite exists to make impossible.

`needs_policy` marks the cases whose correct answer lives only in the policy
document. They are expected to fail at the Phase 4 baseline. That gap is the
measurement, not a defect -- Phase 5 adds retrieval and reruns this identical
file.
"""

from app.eval.cases import EvalCase

ACME = {
    "name": "ACME Incorporated",
    "normalized_name": "acme incorporated",
    "bank_details": "IBAN GB00ACME00000000000001",
}


def routine_history(amounts: list[float], first_number: int) -> list[dict]:
    """Prior payments that should read as entirely unremarkable.

    A case seeded with no history at all is not a neutral case. `get_invoice_history`
    returns count 0, which the agent correctly reads as a first-time payee whose bank
    details nobody has confirmed -- a real reason to escalate, and one that has nothing
    to do with what most of these cases are testing. Left in, it survives Phase 5: the
    agent learns the policy, still escalates for the unrelated reason, and retrieval
    looks like it did nothing.

    Two constraints on what goes in here, both of which bite silently:

    * No amount may equal the invoice under review. `check_duplicate_invoice` matches
      on amount OR invoice number, so a prior payment for the same amount reports as a
      near-duplicate -- trading one confound for a worse one.
    * Invoice numbers must be distinct from each other and from the invoice under
      review, for the same reason.
    """
    return [
        {"amount": amount, "invoice_number": f"INV-{first_number + index}"}
        for index, amount in enumerate(amounts)
    ]

# The tool Phase 5 adds. Named here rather than inline so the baseline and the
# rerun cannot disagree about the spelling -- a mismatch would show up as five
# cases failing the tool grader even after retrieval works, which looks exactly
# like RAG not helping.
SEARCH_POLICY = "search_policy"

CASES = [
    # -- Answerable from the structured tools alone. These should pass at the
    # -- Phase 4 baseline, before any policy retrieval exists.
    EvalCase(
        name="01_clean_approve",
        vendor=ACME,
        invoice={
            "amount": 5000.0,
            "invoice_number": "INV-2001",
            "po_number": "PO-1",
            "raw_text": "ACME Incorporated invoice INV-2001, $5,000, PO-1.",
        },
        past_invoices=routine_history([4800.0, 5200.0, 4950.0], first_number=1901),
        purchase_order={"po_number": "PO-1", "amount": 5000.0},
        expected_decision="approve",
        expected_tools=["lookup_vendor", "get_purchase_order"],
    ),
    EvalCase(
        name="02_exact_duplicate_reject",
        vendor=ACME,
        invoice={
            "amount": 500.0,
            "invoice_number": "INV-1",
            "raw_text": "ACME Incorporated invoice INV-1, $500.",
        },
        past_invoices=[{"amount": 500.0, "invoice_number": "INV-1"}],
        expected_decision="reject",
        expected_tools=["check_duplicate_invoice"],
    ),
    EvalCase(
        # Pairs with 02. Same vendor, same amount, suffixed number -- close
        # enough to be suspicious, not close enough to refuse outright.
        name="03_near_duplicate_escalate",
        vendor=ACME,
        invoice={
            "amount": 500.0,
            "invoice_number": "INV-1-A",
            "raw_text": "ACME Incorporated invoice INV-1-A, $500.",
        },
        past_invoices=[{"amount": 500.0, "invoice_number": "INV-1"}],
        expected_decision="escalate",
        expected_tools=["check_duplicate_invoice"],
    ),
    EvalCase(
        # The drift exists only in `raw_text`; the seeded row carries the real
        # vendor_id, which the agent never sees. It has to read "Acme Inc" off
        # the invoice and resolve it, which is the whole point of the case.
        name="04_vendor_name_drift_approve",
        vendor=ACME,
        invoice={
            "amount": 500.0,
            "invoice_number": "INV-2004",
            "raw_text": "Acme Inc invoice INV-2004, $500, no PO.",
        },
        past_invoices=routine_history([480.0, 520.0], first_number=1904),
        expected_decision="approve",
        expected_tools=["lookup_vendor"],
    ),
    EvalCase(
        # §III.A Step 1 -- the vendor must be correctly set up before goods or
        # services are delivered, so an unknown payee is a stop, not a guess.
        name="05_vendor_not_on_file_escalate",
        vendor=None,
        invoice={"amount": 500.0, "raw_text": "Nonesuch Trading LLC invoice, $500, no PO."},
        expected_decision="escalate",
        expected_tools=["lookup_vendor"],
    ),
    # -- Answerable only from the policy. Expected to fail at the baseline.
    EvalCase(
        # $400 on a $6,000 PO: 6.7%, and the lesser limit is 10% ($600) rather
        # than the $1,000 cap. Inside both, so this one approves.
        name="06_po_variance_within_tolerance_approve",
        vendor=ACME,
        invoice={
            "amount": 6400.0,
            "invoice_number": "INV-2006",
            "po_number": "PO-2",
            "raw_text": "ACME Incorporated invoice INV-2006, $6,400, PO-2.",
        },
        past_invoices=routine_history([6100.0, 6300.0, 5900.0], first_number=1906),
        purchase_order={"po_number": "PO-2", "amount": 6000.0},
        expected_decision="approve",
        expected_tools=["get_purchase_order", SEARCH_POLICY],
        needs_policy=True,
    ),
    EvalCase(
        # Pairs with 06. $3,000 on a $20,000 PO: 15% and $3,000, outside the
        # percentage and the cap both. No reading of §II lets this through.
        name="07_po_variance_outside_tolerance_escalate",
        vendor=ACME,
        invoice={
            "amount": 23000.0,
            "invoice_number": "INV-2007",
            "po_number": "PO-3",
            "raw_text": "ACME Incorporated invoice INV-2007, $23,000, PO-3.",
        },
        past_invoices=routine_history([21000.0, 22500.0, 20500.0], first_number=1909),
        purchase_order={"po_number": "PO-3", "amount": 20000.0},
        expected_decision="escalate",
        expected_tools=["get_purchase_order", SEARCH_POLICY],
        needs_policy=True,
    ),
    EvalCase(
        # The precision test. $1,600 on a $40,000 PO is 4% -- comfortably inside
        # the percentage -- but the lesser of the two limits is the $1,000 cap,
        # and $1,600 clears it. An agent that skims "10 percent" approves this.
        #
        # Deliberately sized under $50,000. §IV.F sends anything above that to
        # the Chief of Accounts, which would hand the case a second, unrelated
        # reason to escalate and let a sloppy reading reach the right answer.
        name="08_po_variance_lesser_of_two_escalate",
        vendor=ACME,
        invoice={
            "amount": 41600.0,
            "invoice_number": "INV-2008",
            "po_number": "PO-4",
            "raw_text": "ACME Incorporated invoice INV-2008, $41,600, PO-4.",
        },
        past_invoices=routine_history([39000.0, 41000.0, 40200.0], first_number=1912),
        purchase_order={"po_number": "PO-4", "amount": 40000.0},
        expected_decision="escalate",
        expected_tools=["get_purchase_order", SEARCH_POLICY],
        needs_policy=True,
    ),
    EvalCase(
        # The policy defers the PO-required threshold to the Procurement
        # Procedures, which are not in the corpus. Correct behaviour is to
        # escalate rather than invent a number -- this tests whether the agent
        # recognizes the edge of what it actually read.
        name="09_large_invoice_no_po_escalate",
        vendor=ACME,
        invoice={
            "amount": 40000.0,
            "invoice_number": "INV-2009",
            "raw_text": (
                "ACME Incorporated invoice INV-2009, $40,000, no purchase order referenced."
            ),
        },
        past_invoices=routine_history([38000.0, 41500.0, 39500.0], first_number=1915),
        expected_decision="escalate",
        expected_tools=[SEARCH_POLICY],
        needs_policy=True,
    ),
    EvalCase(
        name="10_amount_outlier_escalate",
        vendor=ACME,
        invoice={"amount": 25000.0, "raw_text": "ACME Incorporated invoice, $25,000, no PO."},
        past_invoices=[{"amount": 900.0}, {"amount": 1000.0}, {"amount": 1100.0}],
        expected_decision="escalate",
        expected_tools=["get_invoice_history"],
    ),
    EvalCase(
        # §IV.F reads the other way round from the intuition: local currency is
        # the norm and USD is the exception needing authorization. So a EUR
        # invoice is ordinary business and should approve, with the currency
        # handling cited rather than flagged as an anomaly.
        name="11_non_usd_currency_approve",
        vendor=ACME,
        invoice={
            "amount": 4500.0,
            "invoice_number": "INV-2011",
            "raw_text": (
                "ACME Incorporated invoice INV-2011, EUR 4,500.00, PO-5. "
                "Payment in local currency."
            ),
        },
        past_invoices=routine_history([4400.0, 4600.0], first_number=1918),
        purchase_order={"po_number": "PO-5", "amount": 4500.0},
        expected_decision="approve",
        expected_tools=[SEARCH_POLICY],
        needs_policy=True,
    ),
    EvalCase(
        # No amount survived the scan, so there is nothing to check and nothing
        # to be confident about. The confidence floor should carry this one.
        name="12_low_quality_scan_forced_escalate",
        vendor=None,
        invoice={"amount": None, "raw_text": "???ACME??? invoi... $5??.00 ... due ??/??/2026"},
        expected_decision="escalate",
        expected_tools=[],
    ),
]
