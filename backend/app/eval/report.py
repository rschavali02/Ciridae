"""Runs the suite and writes a result file the next run can be diffed against.

Every grader is reported separately rather than folded into one score: outcome
alone cannot tell a correct escalation from an agent that never committed to
anything.
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

        # pass@1 is the per-trial success rate
        passes = [g.passed for g in outcomes]
        results[case.name] = {
            "needs_policy": case.needs_policy,
            "expected_decision": case.expected_decision,
            "pass_at_1": sum(passes) / len(passes),
            "pass_hat_k": all(passes),
            "tool_calls_ok": all(g.passed for g in tools),
            "committed": all(g.passed for g in committed),
            "grounded": all(g.passed for g in grounded),
            "lucky_guesses": sum(
                1 for o, gr in zip(outcomes, grounded) if o.passed and not gr.passed
            ),
            "severities": [g.severity for g in outcomes if g.severity],
            "trials": [
                {
                    "decision": t.decision,
                    "confidence": t.confidence,
                    "tools_called": t.tools_called,
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
    """Print the overall score split by whether a case needs the policy.

    The headline number alone is not legible: structured-only cases should hold
    steady across runs while the policy-dependent ones move.
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
