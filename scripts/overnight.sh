#!/usr/bin/env bash
#
# Unattended execution of the invoice dashboard plan.
#
#   ./scripts/overnight.sh
#
# One Claude Code invocation per task, each with fresh context, gated on the
# test suite between tasks. Stops at the first failure rather than continuing,
# because a broken task compounds -- Task 8 builds on Task 7's transcript
# change, and running six more tasks on top of a bad foundation produces a
# morning's worth of unpickable work.
#
# Deliberately does NOT push. You review the commits and push by hand.
#
# Task 1 is already done. Task 18 is excluded on purpose: it spends real money
# on the Anthropic API and its output is a measurement to be read and thought
# about, not a gate to be passed.

set -uo pipefail

REPO="/Users/rahulchavali/dev/Ciridae"
PLAN="docs/plans/2026-08-12-invoice-dashboard.md"
LOGS="$REPO/.overnight"

# Backend tasks are gated by pytest; frontend tasks by the TypeScript build.
#
# 12b is the cross-task wiring test and is deliberately last in the backend
# list. Every other task passes its own tests while the features they add can
# still fail *between* them -- currency extracted but never written to the row,
# a vendor drafted but resolvable anyway. 12b is the only gate that fails on
# that, so it runs once the whole chain exists.
BACKEND_TASKS=(2 3 4 5 6 7 8 9 10 11 12 12b)
FRONTEND_TASKS=(13 14 15 16 17)

# Where a silent mistake is expensive and the test suite would not catch it, a
# second pass reviews the commit and writes findings to a log for the morning.
# Task 4 is the fraud control, 5 creates payees, 7 rewrites the artifact three
# consumers read, 8 retires an endpoint and adds background execution.
REVIEW_TASKS=(4 5 7 8)

# Claude Code usage limits reset on a rolling window, so being throttled is a
# wait, not a failure. Sleeping through it costs nothing overnight; giving up
# strands the run at whatever task was in flight.
CLAUDE_MAX_ATTEMPTS=10
RATE_LIMIT_WAIT=1200 # 20 minutes

mkdir -p "$LOGS"
cd "$REPO" || exit 1

RUN_LOG="$LOGS/run.log"
say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$RUN_LOG"; }

# Run Claude, waiting out usage limits rather than treating them as errors.
#
# Only retries on a rate limit. Any other non-zero exit is a real failure and is
# returned immediately -- retrying a bad prompt ten times just burns the night.
run_claude() {
  local log="$1" prompt="$2"
  local attempt=1 status resume=""

  while ((attempt <= CLAUDE_MAX_ATTEMPTS)); do
    claude -p "${resume}${prompt}" --dangerously-skip-permissions >"$log" 2>&1
    status=$?

    # Both conditions, not either. This repo's own source discusses Voyage's
    # requests-per-minute cap, so an agent that merely quotes that text on a
    # successful run would otherwise trigger a 20-minute sleep and a pointless
    # retry of work that already landed.
    if ((status == 0)) ||
      ! grep -qiE "rate limit|usage limit|too many requests|limit reached|limit will reset" "$log"; then
      return $status
    fi

    # Surfaced for the morning rather than parsed: the reset time's wording
    # varies between CLI versions, and a wrong parse would sleep for the wrong
    # duration silently. A fixed wait that retries is more predictable.
    local reset
    reset=$(grep -oiE "reset[s]?[^.]{0,40}" "$log" | head -1)
    say "rate limited on attempt $attempt/$CLAUDE_MAX_ATTEMPTS${reset:+ ($reset)}; sleeping ${RATE_LIMIT_WAIT}s"

    # A limit can land mid-task, leaving edited files and no commit. Telling the
    # next attempt to look first is what stops it duplicating half-finished work
    # on a dirty tree.
    resume="A previous attempt at this task was interrupted by a usage limit and may have left partial work. Before doing anything, run 'git status' and 'git log --oneline -3' and read any files the task touches. Continue from where it stopped -- do not redo work that is already committed, and do not duplicate edits already present in the working tree.

"
    sleep "$RATE_LIMIT_WAIT"
    ((attempt++))
  done

  say "still rate limited after $CLAUDE_MAX_ATTEMPTS attempts ($(( CLAUDE_MAX_ATTEMPTS * RATE_LIMIT_WAIT / 3600 ))h); giving up"
  return 1
}

# A dirty tree means uncommitted work that an unattended agent would sweep into
# its own commits, making the morning's diff impossible to attribute.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  say "ABORT: working tree has uncommitted changes. Commit or stash first."
  git status --short
  exit 1
fi

say "starting at $(git rev-parse --short HEAD)"

run_task() {
  local task="$1" gate="$2"
  local log="$LOGS/task-$task.log"
  local before after

  # Captured before Claude runs, so the comparison below actually spans the
  # task. Taking it afterwards would compare HEAD across the gate, which never
  # commits anything, and the no-commit warning could never fire.
  before=$(git rev-parse HEAD)

  say "task $task: implementing"
  run_claude "$log" "Use the superpowers:executing-plans skill.

Implement ONLY Task $task from $PLAN in this repository. Do not start, look ahead to, or partially implement any other task.

Follow the task's steps in order, including writing the test first and watching it fail before implementing. Run the tests it specifies. Commit exactly as the task says, ending the commit message body with:
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

Constraints:
- Do NOT push. Committing is the end of your job.
- Do NOT modify tasks other than $task, and do not 'improve' unrelated code you notice.
- The backend venv is at backend/venv; activate it from the backend directory.
- Run the fast suite as 'python -m pytest -q -m \"not integration\"'. Integration tests call paid APIs -- do not run them.
- If the task is ambiguous or the codebase contradicts it, make the most conservative choice, implement it, and write your reasoning into the commit message rather than stopping."
  local claude_status=$?

  if ((claude_status != 0)); then
    say "task $task: claude exited $claude_status -- see $log. Stopping."
    return 1
  fi

  after=$(git rev-parse HEAD)
  if [[ "$before" == "$after" ]]; then
    # Nothing was committed. Either the task decided it had nothing to do, or it
    # left the work uncommitted -- and the next task would then sweep those
    # edits into its own commit, making the morning's diff unattributable.
    say "task $task: WARNING -- no commit was made"
    if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
      say "task $task: uncommitted changes left behind. Stopping rather than letting the next task absorb them."
      git status --short | tee -a "$RUN_LOG"
      return 1
    fi
  fi

  say "task $task: running $gate gate"
  if ! $gate >"$LOGS/gate-$task.log" 2>&1; then
    say "task $task: GATE FAILED -- see $LOGS/gate-$task.log. Stopping."
    return 1
  fi

  say "task $task: done ($(git log --oneline -1))"
  return 0
}

review_task() {
  local task="$1"
  say "task $task: review pass (high-risk)"
  run_claude "$LOGS/review-$task.raw.log" "Review the most recent commit in this repository for correctness and for scope creep against Task $task of $PLAN.

Write your findings, and nothing else, to $LOGS/review-$task.md. Use headings for Critical / Important / Minor, cite file:line, and state plainly if there is nothing wrong. Do not fix anything, do not commit, do not push -- this is a report for a human to read."
  # A failed review does not stop the run. It is a report for the morning, and
  # losing it is not worth abandoning work that passed its gate.
  return 0
}

pytest_gate() {
  cd "$REPO/backend" || return 1
  # shellcheck disable=SC1091
  source venv/bin/activate
  python -m pytest -q -m "not integration"
  local status=$?
  cd "$REPO" || return 1
  return $status
}

frontend_gate() {
  cd "$REPO/frontend" || return 1
  npm run build
  local status=$?
  cd "$REPO" || return 1
  return $status
}

for task in "${BACKEND_TASKS[@]}"; do
  run_task "$task" pytest_gate || exit 1
  for risky in "${REVIEW_TASKS[@]}"; do
    [[ "$task" == "$risky" ]] && review_task "$task"
  done
done

# Per-task review cannot see across tasks. Each commit can be individually
# correct while the feature they add together does nothing -- currency written
# by one task and dropped by another. 12b gates that as a test; this reads the
# whole backend diff for the same class of gap in the parts no test covers.
say "phase A+B: integration review"
run_claude "$LOGS/review-phase-ab.raw.log" "Review the complete backend diff from commit 57f5c79 to HEAD in this repository, against Phase A and Phase B of $PLAN.

Do not review each commit in isolation -- reviewers have already done that. Look for gaps BETWEEN tasks, where each commit is correct alone but the feature they build together is broken or incomplete. Trace these paths end to end and state whether each actually works:

1. Currency: schema column -> extraction -> written to the invoice row -> read by get_purchase_order -> surfaced in the API response.
2. Vendor onboarding: agent drafts -> lookup_vendor refuses to resolve it -> appears in the pending queue -> human approves -> now resolves.
3. Live observability: agent_runs row created at run start -> updated per tool call -> readable by the activity endpoint while the run is still going.

For each, say plainly whether it works, and if not, name the exact file and line where the chain breaks.

Write your findings and nothing else to $LOGS/review-phase-ab.md. Do not fix anything, do not commit, do not push."
say "phase A+B: integration review written to $LOGS/review-phase-ab.md"

for task in "${FRONTEND_TASKS[@]}"; do
  run_task "$task" frontend_gate || exit 1
done

say "all tasks complete at $(git rev-parse --short HEAD)"
say "commits made overnight:"
git log --oneline "$(git rev-parse --short HEAD)"...master@{1} 2>/dev/null | tee -a "$RUN_LOG" || true
say "NOT pushed. Review, test by hand, then push."
