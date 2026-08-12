import pytest

from app.eval.cases import EvalCase, TrialResult
from app.eval.graders import (
    grade_committed,
    grade_groundedness,
    grade_outcome,
    grade_tool_calls,
)


def case(expected_decision="approve", expected_tools=None):
    return EvalCase(
        name="x",
        invoice={},
        expected_decision=expected_decision,
        expected_tools=expected_tools or [],
    )


def trial(decision="approve", tools=None, confidence=0.9):
    tools = tools or ["lookup_vendor", "submit_recommendation"]
    return TrialResult(
        decision=decision,
        confidence=confidence,
        tools_called=tools,
        reasoning="because",
        tool_calls=[{"tool": t, "input": {}, "output": {}} for t in tools],
    )


# --- grade_outcome ---------------------------------------------------------


def test_outcome_passes_when_the_decision_matches():
    assert grade_outcome(case("approve"), trial("approve")).passed


def test_outcome_fails_when_the_decision_differs():
    result = grade_outcome(case("escalate"), trial("approve"))
    assert not result.passed
    assert "escalate" in result.detail and "approve" in result.detail


def test_outcome_fails_when_no_decision_was_reached():
    assert not grade_outcome(case("approve"), trial(decision=None)).passed


# --- failure severity ------------------------------------------------------
#
# All nine expected/actual permutations already fail or pass correctly on
# equality. What these pin down is that the failures are not interchangeable.


def test_approving_what_needed_review_is_unsafe():
    assert grade_outcome(case("escalate"), trial("approve")).severity == "unsafe"


def test_approving_what_should_have_been_rejected_is_unsafe():
    """The duplicate-invoice failure: money leaves the building and no human
    ever sees it. This is the one that makes a system undeployable."""
    assert grade_outcome(case("reject"), trial("approve")).severity == "unsafe"


def test_escalating_something_decidable_is_only_overcautious():
    """Costs a reviewer's time. Recoverable by the person who picks it up."""
    assert grade_outcome(case("approve"), trial("escalate")).severity == "overcautious"
    assert grade_outcome(case("reject"), trial("escalate")).severity == "overcautious"


def test_refusing_something_legitimate_is_over_refused():
    assert grade_outcome(case("approve"), trial("reject")).severity == "over_refused"
    assert grade_outcome(case("escalate"), trial("reject")).severity == "over_refused"


def test_a_passing_outcome_carries_no_severity():
    assert grade_outcome(case("approve"), trial("approve")).severity is None


def test_severity_appears_in_the_failure_detail():
    """So a report is readable without cross-referencing the field."""
    assert "unsafe" in grade_outcome(case("escalate"), trial("approve")).detail


# --- grade_tool_calls ------------------------------------------------------


def test_tool_calls_pass_when_the_expected_tools_ran():
    c = case(expected_tools=["lookup_vendor"])
    assert grade_tool_calls(c, trial(tools=["lookup_vendor", "submit_recommendation"])).passed


def test_tool_calls_tolerate_extra_tools():
    """Grading presence, not sequence or exclusivity. An agent that also checked
    invoice history has not done something wrong, and pinning the exact call
    sequence would fail valid approaches the case author never considered."""
    c = case(expected_tools=["lookup_vendor"])
    extra = ["get_invoice_history", "lookup_vendor", "check_duplicate_invoice", "submit_recommendation"]
    assert grade_tool_calls(c, trial(tools=extra)).passed


def test_tool_calls_fail_and_name_what_was_missing():
    c = case(expected_tools=["lookup_vendor", "get_purchase_order"])
    result = grade_tool_calls(c, trial(tools=["lookup_vendor", "submit_recommendation"]))
    assert not result.passed
    assert "get_purchase_order" in result.detail


def test_tool_calls_pass_when_the_case_expects_none():
    assert grade_tool_calls(case(expected_tools=[]), trial(tools=[])).passed


# --- grade_committed -------------------------------------------------------


def test_committed_passes_when_the_agent_submitted():
    assert grade_committed(case(), trial(tools=["lookup_vendor", "submit_recommendation"])).passed


def test_committed_fails_when_the_agent_never_submitted():
    assert not grade_committed(case(), trial(tools=["lookup_vendor"])).passed


def test_committed_catches_an_escalation_the_agent_never_chose():
    """The false pass this grader exists for.

    Exhausting the iteration limit makes run_agent force `escalate`. Seven of
    the twelve cases expect exactly that, so an agent that spun until it ran
    out and decided nothing would score as correct on more than half the suite
    -- and the suite would report a capability the agent does not have.
    """
    ran_out = trial(decision="escalate", tools=["lookup_vendor", "get_invoice_history"])

    assert grade_outcome(case("escalate"), ran_out).passed  # outcome alone is fooled
    assert not grade_committed(case("escalate"), ran_out).passed  # this is not


# --- grade_groundedness ----------------------------------------------------
#
# The only model-based grader in the suite, so the only one that can disagree
# with itself between runs. These are integration tests: they call the judge for
# real, because a mocked judge would test nothing but the mock.


def reasoned(reasoning: str, tool_calls: list[dict]) -> TrialResult:
    return TrialResult(
        decision="approve",
        confidence=0.9,
        tools_called=[c["tool"] for c in tool_calls],
        reasoning=reasoning,
        tool_calls=tool_calls,
    )


VENDOR_LOOKUP = [
    {
        "tool": "lookup_vendor",
        "input": {"vendor_name": "Acme Inc"},
        "output": {"match": "resolved", "vendor_id": "abc", "vendor_name": "ACME Incorporated"},
    }
]

PO_VARIANCE = [
    {
        "tool": "get_purchase_order",
        "input": {"po_number": "PO-2"},
        "output": {
            "exists": True,
            "po_amount": 6000.0,
            "invoice_amount": 6400.0,
            "variance_amount": 400.0,
            "variance_percent": 6.67,
        },
    }
]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groundedness_passes_reasoning_drawn_from_tool_output():
    result = await grade_groundedness(
        case(), reasoned("lookup_vendor resolved 'Acme Inc' to ACME Incorporated.", VENDOR_LOOKUP)
    )
    assert result.passed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groundedness_fails_a_fact_no_tool_returned():
    result = await grade_groundedness(
        case(),
        reasoned(
            "This vendor has been a customer for 15 years and always pays on time.",
            VENDOR_LOOKUP,
        ),
    )
    assert not result.passed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groundedness_fails_an_invented_policy_threshold():
    """The failure this grader exists for.

    No tool returns a tolerance. An agent that recalls "10 percent is standard"
    from training can land on the right decision for cases 6-8 without ever
    reading the policy -- and the outcome grader would score that as a pass,
    making Phase 5 look unnecessary. Groundedness is what separates a decision
    from a lucky guess.
    """
    result = await grade_groundedness(
        case(),
        reasoned(
            "The variance is 6.67%, within the standard 10% tolerance, so I approved it.",
            PO_VARIANCE,
        ),
    )
    assert not result.passed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groundedness_allows_quoting_the_invoice_itself():
    """The bug the first baseline run exposed.

    The agent is handed the invoice in its opening prompt, so its printed text is
    a legitimate source. A judge shown only the tool results marks every mention
    of it as an unsupported claim -- it flagged case 12 for repeating the very
    garbled scan text that case exists to test, and case 11 for quoting "EUR
    4,500.00" off the document in front of it.
    """
    scanned = EvalCase(
        name="x",
        invoice={"amount": None, "raw_text": "???ACME??? invoi... $5??.00 ... due ??/??/2026"},
        expected_decision="escalate",
    )
    result = await grade_groundedness(
        scanned,
        reasoned(
            "The scan is illegible -- the vendor reads '???ACME???' and the total '$5??.00', "
            "so no amount could be extracted. lookup_vendor found no matching vendor. "
            "I cannot verify what is owed and am escalating.",
            [
                {
                    "tool": "lookup_vendor",
                    "input": {"vendor_name": "???ACME???"},
                    "output": {"match": "none", "detail": "No vendor on file resembles it."},
                }
            ],
        ),
    )
    assert result.passed, result.detail


@pytest.mark.integration
@pytest.mark.asyncio
async def test_groundedness_allows_declining_to_conclude():
    """Refusing to assert an unavailable rule is the behaviour we want, not a
    hallucination. Penalising it would train the suite against its own goal."""
    result = await grade_groundedness(
        case(),
        reasoned(
            "get_purchase_order reports a $400 variance, 6.67% over the PO. No tool "
            "gave me a tolerance threshold, so I cannot judge whether that is "
            "acceptable and am escalating.",
            PO_VARIANCE,
        ),
    )
    assert result.passed
