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


def grade_outcome(case: EvalCase, trial: TrialResult) -> GradeResult:
    """Did the agent land on the decision a careful reviewer would?"""
    if trial.decision is None:
        return GradeResult(False, "no decision was recorded")
    if trial.decision == case.expected_decision:
        return GradeResult(True, f"decided {trial.decision} as expected")
    return GradeResult(
        False, f"expected {case.expected_decision}, got {trial.decision}"
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
