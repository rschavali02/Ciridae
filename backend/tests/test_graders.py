from app.eval.cases import EvalCase, TrialResult
from app.eval.graders import grade_committed, grade_outcome, grade_tool_calls


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
