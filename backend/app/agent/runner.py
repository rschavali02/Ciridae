"""Wires the agent's tools into the SDK Tool Runner.

The Runner owns the request -> execute -> loop cycle, so there is no hand-written
`while`, no JSON schema array to maintain, and no name->function dispatch table.
Each schema is derived from the wrapper's signature and docstring instead.

That derivation is the constraint shaping this module: a tool's parameters are
exactly what Claude is asked to supply. Anything the model must not choose --
the DB session, the transcript, which invoice row to skip -- is closed over
here rather than declared as a parameter.
"""

import json
from typing import Literal

from anthropic import AsyncAnthropic, beta_async_tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools as tool_impls
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.transcript import RunTranscript
from app.config import settings
from app.models import Invoice

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

MODEL = "claude-opus-5"

# Thinking is on by default on this model and shares max_tokens with the
# response, so a budget sized for the visible answer alone truncates mid-run.
MAX_TOKENS = 16000

MAX_ITERATIONS = 8


def build_tools(session: AsyncSession, transcript: RunTranscript, invoice: Invoice) -> list:
    """Build the tool set for one invoice review.

    Every tool is a closure over `session`, `transcript`, and `invoice`. Tools
    record themselves to the transcript as they run, because the Runner owns the
    loop -- there is no outer iteration of ours in which to record them.
    """

    @beta_async_tool
    async def lookup_vendor(vendor_name: str) -> str:
        """Resolve a vendor name against the vendors on file.

        Call this first on every invoice. The vendor id it returns is required
        by the history and duplicate checks, and a name that resolves to nothing
        -- or ambiguously to several vendors -- is itself a finding.

        Args:
            vendor_name: The vendor name exactly as printed on the invoice.
        """
        out = await tool_impls.lookup_vendor(session, vendor_name=vendor_name)
        transcript.record_tool_call("lookup_vendor", {"vendor_name": vendor_name}, out)
        return json.dumps(out)

    @beta_async_tool
    async def get_invoice_history(vendor_id: str, lookback_days: int = 365) -> str:
        """Summarize what this vendor has previously been paid.

        Call this to judge whether the current amount is normal for them.
        Returns aggregates only -- count, average, range, most recent date --
        covering approved payments, not the invoice under review.

        Args:
            vendor_id: Vendor id returned by lookup_vendor.
            lookback_days: How far back to look. Defaults to one year.
        """
        args = {"vendor_id": vendor_id, "lookback_days": lookback_days}
        out = await tool_impls.get_invoice_history(session, **args)
        transcript.record_tool_call("get_invoice_history", args, out)
        return json.dumps(out)

    @beta_async_tool
    async def check_duplicate_invoice(
        vendor_id: str, amount: float, invoice_number: str | None = None
    ) -> str:
        """Check whether this vendor has already been paid for this invoice.

        Call this on every invoice before approving. Reports an exact match
        (identical number and amount) separately from a near match, because the
        two warrant different responses.

        Args:
            vendor_id: Vendor id returned by lookup_vendor.
            amount: The invoice total.
            invoice_number: The invoice number, if the invoice carries one.
        """
        args = {"vendor_id": vendor_id, "amount": amount, "invoice_number": invoice_number}
        # Bound, not asked of the model: the invoice under review is already a
        # row in `invoices`, and without excluding it a vendor+amount query
        # finds itself, reporting every clean invoice as its own duplicate.
        out = await tool_impls.check_duplicate_invoice(
            session, **args, exclude_invoice_id=invoice.id
        )
        transcript.record_tool_call("check_duplicate_invoice", args, out)
        return json.dumps(out)

    @beta_async_tool
    async def get_purchase_order(po_number: str) -> str:
        """Look up a purchase order and measure how far this invoice diverges.

        Call this whenever the invoice references a PO. Returns the PO amount
        and the variance in both dollars and percent. It does not judge whether
        that variance is acceptable -- that rule is not yours to assume.

        Args:
            po_number: The purchase order number referenced on the invoice.
        """
        # invoice_amount and invoice_currency are bound rather than asked for:
        # both are already known authoritatively, and having the model restate
        # them invites a transcription slip that would corrupt the variance
        # figure -- or the currency comparison -- silently.
        out = await tool_impls.get_purchase_order(
            session,
            po_number=po_number,
            invoice_amount=float(invoice.amount) if invoice.amount is not None else None,
            invoice_currency=invoice.currency,
        )
        transcript.record_tool_call("get_purchase_order", {"po_number": po_number}, out)
        return json.dumps(out)

    @beta_async_tool
    async def draft_vendor(vendor_name: str, bank_details: str | None = None) -> str:
        """Queue an unknown payee for human approval.

        Call this when lookup_vendor finds no vendor on file, so the payee is
        ready for someone to verify. It does not make them payable and does not
        change what you should recommend for this invoice -- an unapproved payee
        still requires human review.

        Args:
            vendor_name: The payee name as printed on the invoice.
            bank_details: Bank details printed on the invoice, if any. These are
                recorded unverified, for a human to check against the vendor.
        """
        args = {"vendor_name": vendor_name, "bank_details": bank_details}
        out = await tool_impls.draft_vendor(session, **args)
        transcript.record_tool_call("draft_vendor", args, out)
        return json.dumps(out)

    @beta_async_tool
    async def submit_recommendation(
        decision: Literal["approve", "reject", "escalate"],
        confidence: float,
        reasoning: str,
    ) -> str:
        """Submit your final decision. Call this exactly once, last.

        A decision below the confidence threshold is routed to a human
        regardless of what you choose here.

        Args:
            decision: approve, reject, or escalate.
            confidence: How sure you are the decision is correct, 0.0 to 1.0.
            reasoning: Why you reached it, citing what the tools returned.
        """
        args = {"decision": decision, "confidence": confidence, "reasoning": reasoning}
        out = tool_impls.submit_recommendation(**args)
        transcript.record_tool_call("submit_recommendation", args, out)
        transcript.record_final(
            decision=out["final_decision"],
            confidence=out["confidence"],
            reasoning=out["reasoning"],
        )
        return json.dumps(out)

    @beta_async_tool
    async def search_policy(query: str) -> str:
        """Search the written AP policy for the clauses governing this invoice.

        Consult the policy before approving anything whose amount, currency,
        missing purchase order, or unfamiliar payee might be governed by a
        written rule. A check the tools cannot perform is not the same as a
        check that passed.

        Thresholds, tolerance bands and required controls live in this document,
        not in your training -- call this rather than asserting a rule from
        memory. Clauses come back with their section headings so you can cite
        the provision you relied on.

        Args:
            query: What you need the policy to tell you, in plain language.
        """
        out = await tool_impls.search_policy_tool(session, query=query)
        transcript.record_tool_call("search_policy", {"query": query}, out)
        return json.dumps(out)

    return [
        lookup_vendor,
        get_invoice_history,
        check_duplicate_invoice,
        get_purchase_order,
        search_policy,
        draft_vendor,
        submit_recommendation,
    ]


def describe_invoice(invoice: Invoice) -> str:
    """Render the invoice for the opening prompt.

    Fields are selected explicitly rather than dumped from `invoice.__dict__`,
    which would ship SQLAlchemy's `_sa_instance_state` into the prompt and
    silently change what the model sees whenever the schema changes.
    """
    return json.dumps(
        {
            "invoice_id": str(invoice.id),
            "text_as_printed": invoice.raw_text,
            "invoice_number": invoice.invoice_number,
            "amount": float(invoice.amount) if invoice.amount is not None else None,
            "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
            "po_number": invoice.po_number,
        },
        indent=2,
    )


async def run_agent(
    session: AsyncSession, invoice: Invoice, source: str = "live"
) -> RunTranscript:
    """Review one invoice and return the transcript of how it was decided."""
    transcript = RunTranscript(invoice_id=invoice.id, source=source)

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        tools=build_tools(session, transcript, invoice),
        messages=[
            {"role": "user", "content": f"Review this invoice:\n{describe_invoice(invoice)}"}
        ],
        max_iterations=MAX_ITERATIONS,
    )

    async for _message in runner:
        # submit_recommendation settles the transcript as a side effect of
        # running, so a recorded decision is how we know the agent has
        # committed. Breaking here skips the wrap-up turn it would otherwise
        # spend narrating a decision already made.
        if transcript.decision is not None:
            break

    if transcript.decision is None:
        # Out of iterations with nothing submitted. Escalate rather than leave
        # the invoice undecided -- silence must never resolve toward payment.
        transcript.record_final(
            decision="escalate",
            confidence=0.0,
            reasoning="Agent did not submit a recommendation within the iteration limit.",
        )

    await transcript.save(session)
    return transcript
