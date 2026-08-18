"""Wires the agent's tools into the SDK Tool Runner.

The Runner owns the request -> execute -> loop cycle, and derives each tool's
schema from the wrapper's signature and docstring. So anything the model must
not choose -- the session, the transcript, which invoice row to skip -- is
closed over here rather than declared as a parameter.
"""

import json
import uuid
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

# Thinking shares this budget with the response, so a budget sized for the
# visible answer alone truncates mid-run.
MAX_TOKENS = 16000

MAX_ITERATIONS = 8


def build_tools(session: AsyncSession, transcript: RunTranscript, invoice: Invoice) -> list:
    """Build the tool set for one invoice review.

    Tools record themselves to the transcript as they run, passing the session
    so each call lands in the agent_runs row immediately -- the dashboard polls
    that row during the 30-60s a review takes.
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

        # Nothing else sets invoices.vendor_id, and the history and duplicate
        # checks both filter on it. Only on `resolved`: pointing the invoice at
        # a drafted vendor would assert a link the approval control withholds.
        if out.get("match") == "resolved":
            invoice.vendor_id = uuid.UUID(out["vendor_id"])
            # Committed here, not left to _flush -- that returns early when the
            # run has no row yet.
            await session.commit()

        await transcript.record_tool_call(
            "lookup_vendor", {"vendor_name": vendor_name}, out, session=session
        )
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
        await transcript.record_tool_call("get_invoice_history", args, out, session=session)
        return json.dumps(out)

    @beta_async_tool
    async def check_duplicate_invoice(
        vendor_id: str, amount: float, invoice_number: str | None = None
    ) -> str:
        """Check whether this invoice has already been paid, or already refused.

        Call this on every invoice before approving. Returns one of four matches,
        because each warrants a different response:

        - "exact": identical invoice number and amount already paid.
        - "near": a prior payment closely resembles this invoice.
        - "previously_rejected": no payment, but a reviewer already refused this
          invoice or one closely resembling it. That decision stands unless
          something has changed, and this tool cannot see why it was made.
        - "none": nothing on file resembles it.

        Args:
            vendor_id: Vendor id returned by lookup_vendor.
            amount: The invoice total.
            invoice_number: The invoice number, if the invoice carries one.
        """
        args = {"vendor_id": vendor_id, "amount": amount, "invoice_number": invoice_number}
        # Bound, not asked of the model: without excluding the invoice under
        # review, a vendor+amount query finds itself.
        out = await tool_impls.check_duplicate_invoice(
            session, **args, exclude_invoice_id=invoice.id
        )
        await transcript.record_tool_call("check_duplicate_invoice", args, out, session=session)
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
        # Amount and currency are bound rather than asked for: both are already
        # known, and restating them invites a slip that corrupts the variance.
        out = await tool_impls.get_purchase_order(
            session,
            po_number=po_number,
            invoice_amount=float(invoice.amount) if invoice.amount is not None else None,
            invoice_currency=invoice.currency,
        )
        await transcript.record_tool_call(
            "get_purchase_order", {"po_number": po_number}, out, session=session
        )
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
        await transcript.record_tool_call("draft_vendor", args, out, session=session)
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
        await transcript.record_tool_call("submit_recommendation", args, out, session=session)
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
        await transcript.record_tool_call(
            "search_policy", {"query": query}, out, session=session
        )
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

    Fields are listed explicitly rather than dumped from `invoice.__dict__`,
    which would ship SQLAlchemy internals into the prompt.
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
    # Before the first tool call: the dashboard polls this row for the length
    # of the run.
    await transcript.begin(session)

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

    try:
        async for _message in runner:
            # submit_recommendation settles the transcript as a side effect, so
            # a recorded decision means the agent has committed. Breaking skips
            # the wrap-up turn narrating a decision already made.
            if transcript.decision is not None:
                break
    except Exception as exc:
        # The tool runner turns in-tool exceptions into error tool_results and
        # keeps looping, so anything reaching here escaped that -- usually a
        # session left needing a rollback by an earlier failed query.
        #
        # Without this the run's row is abandoned at status="running" forever:
        # the ticker polls it indefinitely and the invoice falls out of every
        # queue bucket. A crash is unresolved, and unresolved must read as
        # escalate rather than as "still in progress".
        await session.rollback()
        transcript.record_final(
            decision="escalate",
            confidence=0.0,
            reasoning=f"Agent run failed before reaching a decision: {exc}",
        )
        await transcript.save(session)
        raise

    if transcript.decision is None:
        # Out of iterations with nothing submitted. Silence must never resolve
        # toward payment.
        transcript.record_final(
            decision="escalate",
            confidence=0.0,
            reasoning="Agent did not submit a recommendation within the iteration limit.",
        )

    await transcript.save(session)
    return transcript
