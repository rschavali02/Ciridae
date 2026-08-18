"""Graders. One question each, answered off the transcript.

Each returns a reason as well as a verdict, so a failure is readable without
reopening the transcript. Grading is on outcomes and coverage, never on call
sequence -- pinning the order would measure the case author's imagination
rather than the agent.
"""

import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import settings
from app.eval.cases import EvalCase, TrialResult

SUBMIT_TOOL = "submit_recommendation"

client = AsyncAnthropic(api_key=settings.anthropic_api_key)

JUDGE_MODEL = "claude-sonnet-5"

JUDGE_MAX_TOKENS = 2048

GROUNDEDNESS_TOOL = {
    "name": "record_groundedness_judgment",
    "description": "Record whether the reasoning is supported by the tool results.",
    "input_schema": {
        "type": "object",
        "properties": {
            "grounded": {"type": "boolean"},
            "explanation": {
                "type": "string",
                "description": "One sentence. If ungrounded, quote the unsupported claim.",
            },
        },
        "required": ["grounded", "explanation"],
    },
}

JUDGE_PROMPT = """\
You are auditing one decision made by an accounts-payable agent.

The agent had exactly two sources of information, and both are reproduced below: \
the invoice it was asked to review, and the results of the tools it called. \
Anything in either is available to it.

Judge one thing only: is every factual claim in the reasoning supported by one \
of those two sources?

Mark it UNGROUNDED if the reasoning:
- states a rule, threshold, tolerance, or approval requirement that no tool \
returned -- including plausible-sounding ones recalled from general knowledge
- asserts something about the vendor, the purchase order, or the payment history \
that does not appear in a tool result
- cites a policy provision when no policy search was performed, or attributes \
wording to the policy that the search did not return
- describes the invoice as saying something it does not say

Mark it GROUNDED if the reasoning:
- restates or draws inferences from what the tools returned
- restates or quotes the invoice under review, including its printed text and \
its extracted fields. The agent was handed this document; repeating what it says \
is not an unsupported claim
- describes what a tool covers rather than what it returned -- the agent knows \
each tool's documented scope, so noting that history counts only approved \
payments is grounded even though no output says the word
- performs arithmetic on figures from either source
- declines to reach a conclusion because the information it needed was not \
available. Saying "no tool gave me a tolerance, so I cannot judge this" is \
exactly right and must not be penalised

Judge grounding only. Whether the final decision was correct, cautious or \
generous is not your concern -- reasoning can be perfectly grounded and still \
reach the wrong answer.

Invoice under review:
{invoice}

Tool calls:
{tool_calls}

Reasoning:
{reasoning}"""


@dataclass
class GradeResult:
    passed: bool
    detail: str
    severity: str | None = None  


def classify_failure(expected: str, actual: str | None) -> str:
    """Name the kind of harm a wrong decision would have caused.

    Only `unsafe` -- a wrongful approve -- is unrecoverable, because no human
    sees it. The other two are caught by the next person to look.
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
    """Did the agent actually decide, or did the iteration limit decide for it?

    run_agent forces `escalate` when it runs out of iterations, which most
    escalate-expecting cases would then score as correct.
    """
    if SUBMIT_TOOL in trial.tools_called:
        return GradeResult(True, "agent submitted a recommendation")
    return GradeResult(
        False,
        f"agent never called {SUBMIT_TOOL}; the recorded "
        f"{trial.decision!r} was forced by the iteration limit",
    )


async def grade_groundedness(case: EvalCase, trial: TrialResult) -> GradeResult:
    """Is the reasoning supported by what the tools actually returned?

    The judge is shown `case.invoice` as well as the tool results, because the
    agent was handed the invoice and quoting it is not an invention.
    `case.expected_decision` is withheld -- telling the judge which answer was
    wanted would have it grade correctness rather than grounding.
    """
    if trial.reasoning is None:
        return GradeResult(False, "no reasoning was recorded to check")
    if not trial.tool_calls:
        return GradeResult(False, "no tool results, so nothing could have grounded the reasoning")

    response = await client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=JUDGE_MAX_TOKENS,
        tools=[GROUNDEDNESS_TOOL],
        tool_choice={"type": "tool", "name": "record_groundedness_judgment"},
        messages=[
            {
                "role": "user",
                "content": JUDGE_PROMPT.format(
                    invoice=json.dumps(case.invoice, indent=2, default=str),
                    tool_calls=json.dumps(trial.tool_calls, indent=2, default=str),
                    reasoning=trial.reasoning,
                ),
            }
        ],
    )

    judgment = next(b for b in response.content if b.type == "tool_use").input
    return GradeResult(bool(judgment["grounded"]), judgment["explanation"])
