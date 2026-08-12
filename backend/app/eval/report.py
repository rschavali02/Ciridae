"""Runs the suite and writes a result file the next run can be compared against.

Results go to JSON as well as the terminal, because the Phase 4 baseline only
means something if Phase 5 can be diffed against it rather than remembered.

Three graders run per trial, and all three are reported. Outcome alone is not
enough: seven of the twelve cases expect `escalate`, and `run_agent` forces
exactly that when the agent runs out of iterations without deciding. A run that
looked like 7/12 could be an agent that never committed to anything, so
`committed` is reported beside the score rather than folded into it.
"""

import asyncio
import json
import sys
from collections import Counter

from app.eval.graders import (
    grade_committed,
    grade_groundedness,
    grade_outcome,
    grade_tool_calls,
)
from app.eval.harness import run_case
from app.eval.suite import CASES

TRIALS = 3


async def run_all(label: str) -> dict:
    results: dict[str, dict] = {}

    for case in CASES:
        result = await run_case(case, trials=TRIALS)

        outcomes = [grade_outcome(case, t) for t in result.trials]
        tools = [grade_tool_calls(case, t) for t in result.trials]
        committed = [grade_committed(case, t) for t in result.trials]
        grounded = [await grade_groundedness(case, t) for t in result.trials]

        # pass@1 is the per-trial success rate, not trial zero. Both estimate
        # the same quantity; one of them throws away two thirds of the run.
        passes = [g.passed for g in outcomes]
        results[case.name] = {
            "needs_policy": case.needs_policy,
            "expected_decision": case.expected_decision,
            "pass_at_1": sum(passes) / len(passes),
            "pass_hat_k": all(passes),
            "tool_calls_ok": all(g.passed for g in tools),
            "committed": all(g.passed for g in committed),
            "grounded": all(g.passed for g in grounded),
            # A trial that reached the right decision on reasoning the judge
            # could not trace back to a tool result. Not a pass and not a
            # failure -- a warning that the score is flattering the agent.
            "lucky_guesses": sum(
                1 for o, gr in zip(outcomes, grounded) if o.passed and not gr.passed
            ),
            # Only failures carry a severity, so this is the harm profile of
            # what went wrong -- `unsafe` is the one that matters.
            "severities": [g.severity for g in outcomes if g.severity],
            "trials": [
                {
                    "decision": t.decision,
                    "confidence": t.confidence,
                    "tools_called": t.tools_called,
                    # The full calls, not just their names. Without inputs and
                    # outputs the groundedness judge cannot be re-run against a
                    # saved result, and the harness rolls its transcripts back,
                    # so a run that omits these can only be re-graded by paying
                    # for the whole thing again.
                    "tool_calls": t.tool_calls,
                    "reasoning": t.reasoning,
                    "outcome": o.detail,
                    "tools": tg.detail,
                    "committed": cg.detail,
                    "grounded": gr.passed,
                    "groundedness_detail": gr.detail,
                }
                for t, o, tg, cg, gr in zip(
                    result.trials, outcomes, tools, committed, grounded
                )
            ],
        }

        r = results[case.name]
        print(
            f"{case.name}: pass@1={r['pass_at_1']:.2f} pass^{TRIALS}={r['pass_hat_k']} "
            f"tools_ok={r['tool_calls_ok']} committed={r['committed']} "
            f"grounded={r['grounded']}"
        )
        for i, (trial, outcome) in enumerate(zip(result.trials, outcomes)):
            print(
                f"    trial {i}: {outcome.detail} | confidence={trial.confidence} "
                f"| tools={trial.tools_called}"
            )

        # Flushed per case rather than once at the end. A full run is ~20 minutes
        # of paid model calls, and losing all of it to a crash in the twelfth
        # case would mean paying for the first eleven twice.
        _write(label, results)

    _summarize(label, results)
    print(f"\nwrote {_write(label, results)}")

    return results


def _write(label: str, results: dict) -> str:
    path = f"eval_results_{label}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


def _summarize(label: str, results: dict) -> None:
    """Print the split that Phase 5 is measured on.

    The headline number is close to meaningless on its own here: five cases
    turn on rules the agent cannot read at the baseline, so a low score is the
    expected result rather than a finding. Splitting on `needs_policy` is what
    makes the rerun legible -- structured-only cases should hold steady while
    the policy-dependent ones move.
    """
    structured = [r for r in results.values() if not r["needs_policy"]]
    policy = [r for r in results.values() if r["needs_policy"]]

    def rate(rows: list[dict]) -> str:
        if not rows:
            return "n/a"
        strict = sum(1 for r in rows if r["pass_hat_k"])
        mean = sum(r["pass_at_1"] for r in rows) / len(rows)
        return f"{strict}/{len(rows)} pass^{TRIALS}, {mean:.0%} pass@1"

    print(f"\n=== {label} ===")
    print(f"  overall:         {rate(list(results.values()))}")
    print(f"  structured-only: {rate(structured)}")
    print(f"  policy-dependent:{rate(policy)}")

    severities = Counter(s for r in results.values() for s in r["severities"])
    if severities:
        print("  failure severity: " + ", ".join(f"{k}={v}" for k, v in severities.most_common()))
    unsafe = severities.get("unsafe", 0)
    if unsafe:
        print(f"  !! {unsafe} wrongful approval(s) -- no human ever sees these")

    # The number that decides whether a passing policy-dependent case means
    # anything. An agent with no way to read the policy can still land on the
    # right answer by recalling a plausible threshold; counted as a pass, that
    # makes retrieval look unnecessary when it is exactly what was missing.
    lucky = sum(r["lucky_guesses"] for r in results.values())
    if lucky:
        by_case = [n for n, r in results.items() if r["lucky_guesses"]]
        print(f"  !! {lucky} trial(s) reached the right answer on ungrounded reasoning")
        print(f"     affected: {', '.join(by_case)}")

    never_committed = [name for name, r in results.items() if not r["committed"]]
    if never_committed:
        print(f"  !! never submitted a recommendation: {', '.join(never_committed)}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    asyncio.run(run_all(label))
