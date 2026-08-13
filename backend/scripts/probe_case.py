"""Run one eval case and print its trace, for iterating on prompts cheaply.

Usage:
    PYTHONPATH=. python scripts/probe_case.py 09 [trials]

A full suite run is ~25 minutes and ~$4. Discovering there that a prompt change
did not land is an expensive way to find out; one case is pennies.
"""

import asyncio
import sys

from app.eval.graders import (
    grade_committed,
    grade_groundedness,
    grade_outcome,
    grade_tool_calls,
)
from app.eval.harness import run_case
from app.eval.suite import CASES


async def main(prefix: str, trials: int) -> None:
    case = next(c for c in CASES if c.name.startswith(prefix))
    print(f"{case.name}  (expects {case.expected_decision})\n")

    result = await run_case(case, trials=trials)

    for i, trial in enumerate(result.trials):
        outcome = grade_outcome(case, trial)
        grounded = await grade_groundedness(case, trial)
        mark = "PASS" if outcome.passed else "FAIL"
        print(f"[{mark}] trial {i}: {trial.decision} (confidence {trial.confidence})")
        print(f"        outcome   : {outcome.detail}")
        print(f"        tools     : {grade_tool_calls(case, trial).detail}")
        print(f"        committed : {grade_committed(case, trial).detail}")
        print(f"        grounded  : {grounded.passed} -- {grounded.detail[:150]}")

        searches = [c for c in trial.tool_calls if c["tool"] == "search_policy"]
        if searches:
            for call in searches:
                sections = [c["section"] for c in call["output"].get("clauses", [])]
                print(f'        searched  : "{call["input"]["query"]}"')
                print(f"                    -> {sections}")
        else:
            print("        searched  : (never called search_policy)")
        print()

    passed = sum(1 for t in result.trials if grade_outcome(case, t).passed)
    print(f"{passed}/{len(result.trials)} trials passed")


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else "09"
    trials = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    asyncio.run(main(prefix, trials))
