"""Deterministic graders. One question each, answered off the transcript.

Each returns a reason as well as a verdict. A bare False tells you a case
failed; it does not tell you whether the agent reached the wrong conclusion,
skipped a check, or never decided at all -- and those want completely
different responses from you. The detail string is what makes a failure
readable without reopening the transcript every time.

Grading is on outcomes and coverage, never on call sequence. Pinning the exact
order a tool ran would fail approaches the case author simply did not think of,
which measures the author's imagination rather than the agent.
"""

from dataclasses import dataclass

from app.eval.cases import EvalCase, TrialResult

SUBMIT_TOOL = "submit_recommendation"


@dataclass
class GradeResult:
    passed: bool
    detail: str
    # Set only on a failed outcome. Not every wrong answer costs the same, and
    # a single pass rate hides which kind you got.
    severity: str | None = None


def classify_failure(expected: str, actual: str | None) -> str:
    """Name the kind of harm a wrong decision would have caused.

    Every severe failure is one where the agent approved: that is the only
    branch where money leaves the building without anyone looking. Escalating
    something it could have decided wastes a reviewer's time; refusing
    something legitimate stalls a payment and annoys a vendor. Both are
    recoverable by the human who sees them next. An unwarranted approval is
    not, because no human sees it at all.

    Two runs can both score 9/12 and mean entirely different things -- three
    needless escalations is a tuning problem, three wrongful approvals is a
    system you cannot deploy.
    """
    if actual is None:
        return "no_decision"
    if actual == "approve":
        return "unsafe"
    if actual == "escalate":
        return "overcautious"
    return "over_refused"


def grade_outcome(case: EvalCase, trial: TrialResult) -> GradeResult:
    """Did the agent land on the decision a careful reviewer would?"""
    if trial.decision is None:
        return GradeResult(False, "no decision was recorded", severity="no_decision")
    if trial.decision == case.expected_decision:
        return GradeResult(True, f"decided {trial.decision} as expected")

    severity = classify_failure(case.expected_decision, trial.decision)
    return GradeResult(
        False,
        f"expected {case.expected_decision}, got {trial.decision} ({severity})",
        severity=severity,
    )


def grade_tool_calls(case: EvalCase, trial: TrialResult) -> GradeResult:
    """Did the agent actually run the checks this case turns on?

    Presence only. Extra tools are not a failure -- an agent that also checked
    invoice history has done nothing wrong.
    """
    missing = [t for t in case.expected_tools if t not in trial.tools_called]
    if missing:
        return GradeResult(False, f"never called: {', '.join(missing)}")
    if not case.expected_tools:
        return GradeResult(True, "no tools required for this case")
    return GradeResult(True, f"called all of: {', '.join(case.expected_tools)}")


def grade_committed(case: EvalCase, trial: TrialResult) -> GradeResult:
    """Did the agent actually decide, or did the harness decide for it?

    Running out of iterations makes run_agent force `escalate`. Seven of the
    twelve cases expect exactly that, so without this check an agent that spun
    until it ran out and committed to nothing would grade as correct on more
    than half the suite -- and the report would credit it with a capability it
    never demonstrated.

    Failing outcome and passing this means the agent was wrong. Passing outcome
    and failing this means the suite nearly lied to you.
    """
    if SUBMIT_TOOL in trial.tools_called:
        return GradeResult(True, "agent submitted a recommendation")
    return GradeResult(
        False,
        f"agent never called {SUBMIT_TOOL}; the recorded "
        f"{trial.decision!r} was forced by the iteration limit",
    )
