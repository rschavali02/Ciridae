# Architecture & decision Q&A cheat sheet

Prep notes for defending design choices live, not a spec. Each item: the
choice, what it costs, and where to point if pushed.

## Safety vs. automation rate

- **Confidence floor overrides the agent's own decision**
  (`submit_recommendation`, `backend/app/agent/tools.py`, threshold in
  `backend/app/config.py`). Below the threshold the decision becomes
  `escalate` regardless of what the agent asked for — applies to rejections
  too, not just approvals, since wrongly rejecting a legitimate invoice also
  has a cost.
  Cost: a genuinely well-calibrated "approve" on an edge case still gets
  forced to escalate. Trades straight-through-processing rate for a hard
  floor. See "Why 0.7" below — this floor is weaker than it looks.

- **Similarity scores withheld from the agent** (`lookup_vendor`,
  `search_policy`). The tool decides "is this match good enough," not the
  model.
  Why: in an actual live run the agent cited a raw similarity score as its
  reason for lowering confidence — a well-matched vendor scored 0.42 on a
  0–1 scale, which *reads* low to a model even though it's a strong match
  for trigram similarity. Handing over the number invited an uncalibrated
  re-judgment of an already-calibrated decision.
  Cost: the agent can't explain *why* a match is uncertain, only that it is.

- **PO variance arithmetic lives in code; the acceptability threshold lives
  in policy text (RAG).** `get_purchase_order` computes variance, never
  judges it — `search_policy` is where the tolerance rule has to come from.
  Why: keeps the RAG measurement meaningful (agent must retrieve the clause,
  can't hardcode it) and keeps the threshold able to change when Finance
  reissues the policy, without a code change.
  Cost: if the *computation* itself needs to change — e.g. adding FX
  conversion — that's a code change, not a policy edit. "Policy is the
  source of truth" only holds for thresholds, not for how numbers are
  derived.

- **New vendors always require a human approval loop**
  (`draft_vendor` → `pending_approval` → separate `/vendors/{id}/approve`),
  regardless of how clean the invoice looks.
  Why: bank details on an unknown-vendor invoice come from the one document
  an attacker controls. This is the system's primary fraud control.
  Cost: onboarding friction on every legitimately new vendor — no fast path,
  by design.

## Data / retrieval scope

- **RAG is policy-only — invoice text is never embedded into `documents`.**
  Why: prevents the agent from retrieving another invoice's numbers while
  reasoning about the one in front of it.
  Cost: RAG can't answer "does this resemble other invoices we've seen" —
  that's `get_invoice_history` / `check_duplicate_invoice` instead, which
  are exact-match/aggregate, not semantic.

- **Exact brute-force vector search, no ANN index.** Confirmed via
  `EXPLAIN`: `Seq Scan` + `Sort`, no `ivfflat`/`hnsw` index exists on
  `documents.embedding`.
  Why it's fine now: 25 chunks, sub-millisecond either way — an index would
  add overhead, not save time, at this scale.
  Where it breaks: once the corpus is in the thousands of chunks, this
  becomes the bottleneck. Fix is an `hnsw` index (or Pinecone), which trades
  exactness for approximate sublinear search — and at that point you also
  want metadata filtering (scope search to the current policy doc) since
  cross-document noise becomes the bigger failure mode than search speed.

## Stubs and infra

- **`get_purchase_order` and "Auto-approve to Payments" are both stand-ins**
  for unbuilt ERP/payments integrations — real tool contracts, seeded/fake
  backends (one PO row: `PO-88213`, $5,000 USD). State this plainly before
  someone finds it as a gotcha.

- **Only Postgres is containerized today** (`backend/docker-compose.yml`).
  Backend runs from a local venv, frontend from local `npm run dev` — not
  "clone and `docker compose up`" yet for anyone else.

- **Malformed tool input escalates rather than raising**
  (`submit_recommendation`). No invoice silently disappears — but a genuine
  bug in how the agent calls a tool is indistinguishable in the UI from a
  normal escalation. Honest gap if asked "how would you know the agent was
  misbehaving vs. correctly cautious": nothing currently separates those two
  today.

## Why 0.7, specifically — is there justification beyond "feels right"?

Short answer: partially, and it's worth being honest about the gap rather
than overselling it.

**There is a real, measured signal in the eval data**
(`backend/eval_results_baseline.json`, `eval_results_with_rag.json`) — self-reported
confidence on trials where the agent's decision matched the expected one
averages **0.83–0.85**; on trials where it didn't, confidence averages
**0.68–0.72**. That's a consistent ~0.15 gap between the two populations,
and 0.7 sits between them — same *spirit* as how `SIMILARITY_THRESHOLD` in
`lookup_vendor` was picked (measured against real data, not by feel).

**But unlike that vendor threshold, this is not a clean empty gap — the two
distributions overlap heavily, and the floor misses more than it catches:**

| Run | Wrong trials | Wrong trials ≥ 0.7 (floor misses) |
|---|---|---|
| Baseline | 9 | 5 |
| With RAG | 4 | 2 |

**The sharpest example: the confidence floor caught none of the actually
dangerous failures.** In the baseline run, the agent approved a $40,000
invoice with no PO on all three trials at confidence **0.80–0.82** —
comfortably above the 0.7 floor. Its reasoning was internally sound ("all
applicable checks came back clean") — it was confidently missing a rule it
had no way to know about, not uncertain. The floor only catches cases where
the *agent itself* signals doubt; it does nothing for a model that's
consistently, plausibly wrong. What actually fixed case 09 was giving the
agent `search_policy` (RAG), not the confidence floor — see
`finalResults.md`.

**How to frame this live:** 0.7 is directionally supported — it's picked
from the right side of a real, measured separation between correct and
incorrect trials, not an arbitrary round number. But it should be described
as a backstop for the agent's own self-reported uncertainty, not a
calibrated error detector. The eval data shows most of the dangerous
failures in this project were confidently wrong, and the fix for those was
better grounding (RAG), not a stricter floor. If pushed further: the honest
next step would be recalibrating the floor per-failure-mode (e.g., a
stricter floor specifically on `approve` when no PO is referenced) rather
than one global number — that's future work, not implemented.

## Don't defend 0.7 — reframe what's actually doing the safety work

Defending 0.7 as "the right number" invites exactly the poke-hole-in-it
question above. The stronger, more accurate framing: **the tool calls
(vendor resolution, duplicate check, PO variance, RAG-grounded policy)
produce correctness; 0.7 is a coarse triage signal for the agent's own
self-doubt, not the thing keeping bad decisions from shipping.**

That framing isn't just more defensible rhetorically — it's backed by a
fact already true in the code, not an aspiration: **`Invoice.status` is
mutated in exactly one place in the whole backend**
(`record_human_decision`, `backend/app/main.py:394`), reached only from the
`/invoices/{id}/approve` and `/invoices/{id}/reject` endpoints. The agent's
own decision — even `approve` at 0.93 confidence — never moves an invoice
off `pending` by itself. Every invoice, regardless of the agent's decision
or confidence, sits in the queue until a human explicitly clicks
approve/reject. "Auto-approve to Payments" on the architecture diagram is
the target state; today it's 100% human-gated, full stop. That's a
stronger, verified claim than "0.7 triages for review" — say it as fact,
not as design intent.

## The residual risk this doesn't solve: confidently-wrong output in front of a human

Mandatory human review is real, but it doesn't mean it's *effective*
against a confidently wrong agent. The $40,000 no-PO case is the concerning
shape precisely because it reads well to a reviewer — fluent, plausible
reasoning ("all applicable checks came back clean") at 0.82 confidence.
That's exactly the condition under which a human is most likely to
rubber-stamp rather than scrutinize (automation bias), not a hypothetical
edge case.

So the honest position is: **the system's safety today rests on reviewer
vigilance holding up against confident-sounding wrong answers, and nothing
in the UI currently helps a reviewer catch that case specifically.** The
transcript shows the reasoning, but nothing flags "large invoice, no PO
referenced" as a pattern worth extra scrutiny independent of what the agent
concluded. If asked "what's next": a pattern-based forced-escalation rule
(e.g., large-dollar-no-PO) that triggers independent of agent confidence,
rather than relying on the floor or trusting the reviewer to catch a
confident-sounding wrong answer unaided.
