# Did retrieval earn its place? — measured

Companion to `initialResults.md`, which recorded the baseline before the agent could
read the AP policy. Phase 5 added `search_policy` as a single additive change — one
tool, two prompt lines — and re-ran the identical twelve cases.

Raw data: `backend/eval_results_baseline.json` and `backend/eval_results_with_rag.json`.

## Headline

| Metric | Baseline | With retrieval | |
|---|---|---|---|
| pass^3 | 9 / 12 | **10 / 12** | +1 |
| pass@1 | 75% | **89%** | +14pts |
| policy-dependent cases (5) | 2/5 | **4/5** | +2 |
| structured-only cases (7) | 7/7 | 6/7 | −1 |
| **unsafe approvals** | **3** | **0** | **−3** |
| tool coverage (`tools_ok`) | 7/12 | **12/12** | +5 |

## Per case

| # | Case | base | rag | |
|---|---|---:|---:|---|
| 01 | clean_approve | 1.00 | 1.00 | |
| 02 | exact_duplicate_reject | 1.00 | 1.00 | |
| 03 | near_duplicate_escalate | 1.00 | 1.00 | |
| 04 | vendor_name_drift_approve | 1.00 | **0.67** | regressed |
| 05 | vendor_not_on_file_escalate | 1.00 | 1.00 | |
| 06 | po_variance_within_tolerance_approve | **0.00** | **1.00** | flipped |
| 07 | po_variance_outside_tolerance_escalate | 1.00 | 1.00 | |
| 08 | po_variance_lesser_of_two_escalate | 1.00 | 1.00 | |
| 09 | large_invoice_no_po_escalate | **0.00** | **1.00** | flipped |
| 10 | amount_outlier_escalate | 1.00 | 1.00 | |
| 11 | non_usd_currency_approve | 0.00 | 0.00 | unchanged |
| 12 | low_quality_scan_forced_escalate | 1.00 | 1.00 | |

## What retrieval bought

**Three unsafe approvals became zero.** This is the result that matters. At baseline
the agent approved a $40,000 invoice with no purchase order on all three trials, at
confidence 0.80–0.82 — above the escalation floor, so no human would ever have seen
them. Its reasoning was sound on its own terms: *"All applicable checks came back
clean."* Every check it could run had passed. The rule it needed existed only in the
policy.

With retrieval it escalates 3/3, having called `search_policy` four times with
successively refined queries and cited `III. Procedures`. Every remaining failure in
the suite is `overcautious` — a reviewer loses a few minutes, and the decision is
recoverable because a human sees it.

**Case 06 is the clean test that a rule was read rather than guessed.** §II gives
`min(10% × $6,000, $1,000) = $600` against a $400 variance. Baseline 0/3, retrieval
3/3. Nothing else changed between the runs.

**Tool coverage went 7/12 to 12/12.** There is no case where the agent had
`search_policy` available and failed to reach for it. That was a live risk: the
baseline prompt deliberately never mentioned a policy, so an under-stated instruction
could have left the tool unused and the whole comparison measuring nothing.

**The agent never invented a threshold** in either run. `get_purchase_order` reports
variance without judging it, and that restraint held — at baseline the agent said it
had no rule to apply and escalated; with retrieval it applied the rule it found. So
the improvement is attributable to retrieval rather than to a lucky recall.

## The two cases that did not go to plan

Both are more interesting than the score suggests.

### Case 04 — the predictable cost of fixing case 09

A $500 invoice with a drifted vendor name and no PO. Baseline 3/3 approve; with
retrieval 2/3, one trial escalating.

The failing trial searched *"When is a purchase order required for an invoice?
Threshold for non-PO invoices"*, found that §III defers the threshold to the
Procurement Procedures — **a document not in the corpus** — and escalated because it
could not confirm $500 sits below a limit it cannot read.

That is the same reasoning that fixed case 09, applied to a small invoice. Cases 04
and 09 differ only in amount; both lack a PO, and the corpus never states the
threshold that separates them. The agent has no principled basis for treating $500
differently from $40,000, so it is guessing in both directions — and it happens to
guess right on 04 two times in three.

This is a genuine trade-off rather than a bug: the instruction that eliminated three
unsafe approvals costs roughly one overcautious escalation on small no-PO invoices.
In AP terms that is a good trade. An unwarranted approval is unrecoverable; an
unnecessary escalation costs a reviewer a minute.

### Case 11 — a broken task, not an agent failure

Expects approve; 0/3 in both runs. But `tools_ok` passes, so the agent *did* search
the policy and escalated anyway. Its reasoning:

> "The invoice is denominated in EUR (\"EUR 4,500.00\"), but no tool reported a
> currency for the purchase order or for the historical payments. `get_purchase_order`
> compared 4,500 against 4,500 as bare numerals; if PO-5 is denominated in a different
> currency, that reported 0.0% variance is a false match... This is a check the tools
> could not perform, not a check that passed."

The agent is right, and the eval case is wrong. **`invoices.amount` and
`purchase_orders.amount` are bare `Numeric(12,2)` with no currency column anywhere in
the schema.** The system cannot represent the fact the case turns on. Comparing a EUR
invoice against a currency-less PO genuinely is a false match, and §IV.F's rule about
paying in the business unit's local currency cannot be evaluated when nothing records
which currency anything is in.

Per Step 7 of `AI-Agent-Evals.md`, a task the agent cannot solve through no fault of
its own is a broken task. The fix is a schema change — a currency column on invoices
and purchase orders — not prompt tuning. Until then, case 11 asserts an outcome the
data model cannot support.

Worth noting the agent reached this by generalising the prompt line added for case 09
("a check the tools cannot perform is not the same as a check that passed") to a
situation it was never written for.

## Honest caveats

**One run per side.** Both numbers come from a single 12×3 run. The agent is
stochastic: across two baseline runs, case 09 moved 1/3 → 0/3 while the deterministic
core stayed identical at 9/12 and 7/7. Single-case differences of one trial are
within noise; the direction and size of the aggregate change are not.

**Groundedness flags 01, 05 and 12** in the retrieval run — cases where the agent
reasons from the invoice and vendor lookup rather than from policy text. The judge was
already miscalibrated once in this project, and these were not investigated.

**The prompt was changed alongside the tool.** Two lines: reframing the purchase-order
check from "if one is referenced" to "whether one was required", and adding that a
check the tools cannot perform is not a check that passed. Both are general AP
practice rather than case-shaped, and the change was written once and probed once —
there was no cycle of tuning wording until a case went green. But the comparison is
retrieval *plus* those two lines, not retrieval alone.

**Cost.** Baseline ~$4.15; the retrieval run roughly $10, since consulting the policy
roughly doubles the model calls per trial and every retrieved clause rides along in
the context of each subsequent turn.

## What would come next

Not done, and not required for the result above to stand:

1. **Add a currency column** to `invoices` and `purchase_orders`, then revisit case 11.
2. **Decide what case 04 should assert.** If the corpus genuinely cannot establish
   when a PO is required, escalating a no-PO invoice may be correct behaviour and the
   case's expectation is what should change.
3. **Recalibrate the groundedness judge** against the three flagged cases.
4. **Prompt caching.** The system prompt and tool definitions are byte-identical
   across every turn; cached reads cost about a tenth of full price and would cut the
   run cost substantially without changing a single decision.

---

# Re-run after the currency fix

The comparison above was measured on the day the currency columns landed, and
the recorded run predates them — the `get_purchase_order` output saved in
`eval_results_with_rag.json` for case 11 carries no `po_currency`,
`invoice_currency` or `currency_match` field at all. The suite was re-run
against the current code, unchanged in every other respect, as
`backend/eval_results_with_rag_v2.json`.

| Metric | Baseline | With retrieval | Current |
|---|---|---|---|
| pass^3 | 9 / 12 | 10 / 12 | **11 / 12** |
| pass@1 | 75% | 89% | **92%** |
| policy-dependent (5) | 2/5 | 4/5 | **5/5** |
| structured-only (7) | 7/7 | 6/7 | 6/7 |
| **unsafe approvals** | **3** | **0** | **0** |
| tool coverage | 7/12 | 12/12 | 12/12 |

## Case 11 — the broken task, now fixed

0/3 → **3/3**. Step 1 of "What would come next" above called for a currency
column on `invoices` and `purchase_orders`; that is what changed.
`get_purchase_order` now returns `po_currency: "EUR"`, `invoice_currency:
"EUR"`, `currency_match: true`, so the agent can confirm it is comparing like
with like instead of refusing a comparison it could not verify. The agent's
original objection is worth restating, because it was correct: a 0.0% variance
computed across unknown units is a false match, not a passed check.

## Case 04 — now consistent rather than intermittent

0.67 → **0.00**. All three trials escalate, at 0.68 confidence — below the
floor, so the system would have forced escalation regardless of what the agent
asked for.

This reads as confirmation rather than regression. Previously the agent
guessed, and happened to guess "approve" two times in three; it now reliably
declines to guess. The reasoning is the same each time:

> All the checks I could actually perform came back clean, but the one rule the
> decision turns on cannot be verified with the tools available.

§III defers the PO-required threshold to the UNFPA Procurement Procedures,
which is not in the corpus, so nothing available to the agent establishes that
$500 sits below it. Step 2 of "What would come next" asked whether case 04's
expectation was what should change. It has deliberately been left as `approve`:
rewriting an expected answer after watching it fail is how a suite stops
measuring anything, and 11/12 with an explained miss is worth more than 12/12
bought that way. The real fix is the missing document or an ERP integration
exposing the requisition threshold — not a change to the agent or the case.

## What did not improve

Lucky guesses went 5 → 7, and cases 05, 11 and 12 are flagged ungrounded.
At least one of those is the judge rather than the agent: on case 11 it marked
the reasoning unsupported for saying an extracted `po_number` field was null,
on the grounds that no such field appears in what it was shown. That is the
judge misreading "the field is empty" as "the field does not exist". Step 3 —
recalibrate the groundedness judge — remains open, and these numbers should not
be cited until it is done.
