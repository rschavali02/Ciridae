# Baseline results — the numbers to beat

Phase 4 baseline, recorded 2026-08-12. This is the agent running on its **five structured
tools only**: `lookup_vendor`, `get_invoice_history`, `check_duplicate_invoice`,
`get_purchase_order`, `submit_recommendation`. It has no way to read the AP policy.

Phase 5 adds `search_policy` as a single additive change and re-runs this identical suite.
The point of recording this file is to make "did retrieval earn its place?" a number that was
measured rather than a claim that was asserted.

Raw data: `backend/eval_results/baseline.json`.

## Headline

| Metric | Baseline |
|---|---|
| pass^3 | **9 / 12** |
| pass@1 | **75%** |
| structured-only cases (7) | **7/7 pass^3, 100% pass@1** |
| policy-dependent cases (5) | **2/5 pass^3, 40% pass@1** |
| **unsafe approvals** | **3** |
| ungrounded-but-correct trials | 3 |

`pass@1` is the per-trial success rate; `pass^3` requires all three trials to pass. The second
is the one that matters here — a high-confidence wrong call skips human review entirely, so a
case that passes only sometimes is exactly the reliability failure the confidence floor exists
to catch.

## Per case

| # | Case | pass@1 | pass^3 | tools | grounded | severity |
|---|---|---:|---|---|---|---|
| 01 | clean_approve | 1.00 | yes | yes | yes | |
| 02 | exact_duplicate_reject | 1.00 | yes | yes | yes | |
| 03 | near_duplicate_escalate | 1.00 | yes | yes | yes | |
| 04 | vendor_name_drift_approve | 1.00 | yes | yes | no | |
| 05 | vendor_not_on_file_escalate | 1.00 | yes | yes | yes | |
| 06 | po_variance_within_tolerance_approve | **0.00** | no | no | no | overcautious · policy |
| 07 | po_variance_outside_tolerance_escalate | 1.00 | yes | no | yes | policy |
| 08 | po_variance_lesser_of_two_escalate | 1.00 | yes | no | no | policy |
| 09 | large_invoice_no_po_escalate | **0.00** | no | no | no | **unsafe ×3** · policy |
| 10 | amount_outlier_escalate | 1.00 | yes | yes | no | |
| 11 | non_usd_currency_approve | **0.00** | no | no | no | overcautious · policy |
| 12 | low_quality_scan_forced_escalate | 1.00 | yes | yes | yes | |

`tools` fails on cases 6-9 and 11 because each expects `search_policy`, which does not exist at
the baseline. That is the gap being measured, not a defect.

## What the numbers say

**The split is clean.** Everything answerable from company records is perfect; everything that
turns on a written rule is at 40%. The agent is not weak — it is uninformed, and the boundary
falls exactly where the tools stop.

**Case 09 is the serious one.** All three trials **approved a $40,000 invoice carrying no
purchase order**, at confidence 0.82 / 0.80 / 0.82. Every one clears the 0.7 escalation floor,
so the server-side override never fires and no human ever sees them. The reasoning is sound on
its own terms:

> "3 prior approved payments averaging $39,666.67 with a range of $38,000-$41,500 ... the
> $40,000 invoiced here falls inside that range and within roughly 1% of the average, so the
> amount is normal for this vendor ... All applicable checks came back clean."

Nothing there is false. Every check it can run does pass. It approves because it cannot know a
purchase order was required — that rule exists only in the policy. This is the sharpest
argument for retrieval in the whole suite: the failure is missing information, and the agent is
confidently wrong *because* all its available evidence points the other way.

**Cases 6/7/8 behave as the pairing intends.** 07 and 08 pass, 06 fails 0/3. The agent escalates
everything PO-related because it has no tolerance rule, which is right twice and wrong once.
Only genuine retrieval makes all three correct simultaneously — that is what the triple is for,
and why 06 is the case to watch.

**The agent did not invent a threshold.** No trial claimed a "standard 10%" tolerance from
memory. `get_purchase_order` deliberately reports variance without judging it, and that
restraint held: the agent repeatedly said it had no rule to apply and escalated instead. Good
result for the tool design, and it means any post-retrieval improvement is attributable to
retrieval rather than to a lucky recall.

## What Phase 5 has to demonstrate

Beating 9/12 is not sufficient on its own. The claims worth making:

1. **06 flips to approve** — §II's `min(10% × $6,000, $1,000) = $600` limit against a $400
   variance. The single clearest test that a rule was read rather than guessed.
2. **09 stops approving** — the policy defers the PO threshold to the Procurement Procedures, a
   document *not* in the corpus, so the correct behaviour is to escalate rather than invent a
   number. Watch for the agent fabricating a threshold instead; groundedness is the only grader
   that would catch that.
3. **11 flips to approve** — §IV.F makes local currency the norm and USD the exception.
4. **07 and 08 keep passing, but for the right reason** — visible as `tools` going green and
   groundedness holding.
5. **Structured-only stays at 7/7.** Retrieval can hurt: an agent handed five clauses may
   over-apply one and start escalating clean invoices. A drop here is a real finding about tool
   and prompt design, not a rounding error.

## Configuration

Both sides of the comparison must hold these constant.

| | |
|---|---|
| agent model | `claude-opus-5` (thinking on by default, effort left at the default) |
| max_tokens / max_iterations | 16000 / 8 |
| confidence escalation floor | 0.7 |
| trials per case | 3 |
| groundedness judge | `claude-sonnet-5` |

Reproduce with:

```bash
cd backend && PYTHONPATH=. python -u -m app.eval.report baseline
```

Roughly 25 minutes and ~$4.15 per full run, measured. Each trial's full tool calls are saved,
so re-grading a completed run costs ~$0.22 instead of a re-run.

## Caveats

**Trial isolation.** Each trial runs inside an outer transaction with the session in savepoint
mode, so even the agent's own commits are rolled back. Nothing persists between trials, and
nothing persists after the run — which is also why re-grading needs the saved `tool_calls`.

**The groundedness judge was fixed mid-Phase-4.** The first run graded reasoning against tool
results alone, while the agent is also handed the invoice in its opening prompt — so quoting the
invoice read as an unsupported claim, producing 14 false "ungrounded" flags. The judge now sees
both sources. That first run is kept as `backend/eval_results/baseline_v1_broken_judge.json`;
its deterministic graders agreed with this run at 9/12 and 7/7 structured, which is a free read
on run-to-run stability.

**Run-to-run variance is real.** The agent is stochastic. Across the two runs the deterministic
core was identical, and case 09 moved from 1/3 to 0/3 — sharpening the finding rather than
contradicting it.

**Known noise.** The runner emits `RuntimeError: generator didn't stop after athrow()` warnings
because `run_agent` breaks out of the Tool Runner's loop once a decision lands without closing
the generator. Cosmetic; results unaffected.
