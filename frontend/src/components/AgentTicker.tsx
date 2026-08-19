import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { getActivity } from "../api";
import { queryKeys } from "../queryKeys";

const LABELS: Record<string, (input: any) => string> = {
  lookup_vendor: (i) => `Resolving vendor "${i.vendor_name}"…`,
  get_invoice_history: () => "Checking payment history…",
  check_duplicate_invoice: () => "Checking for duplicates…",
  get_purchase_order: (i) => `Looking up ${i.po_number}…`,
  search_policy: (i) => `Searching policy: "${i.query}"…`,
  draft_vendor: (i) => `Drafting new vendor "${i.vendor_name}" for approval…`,
  submit_recommendation: () => "Reaching a decision…",
};

// `tool` and `input` are nullable: the activity endpoint reads them off the
// latest transcript entry with .get(), so a malformed entry yields null rather
// than failing the poll.
function describeStep(tool: string | null, input: Record<string, unknown> | null): string {
  if (!tool) return "Working…";
  const label = LABELS[tool];
  return label ? label(input ?? {}) : `Running ${tool}…`;
}

interface AgentTickerProps {
  invoiceId: string;
  /** Called once, the first time polling observes a settled run. */
  onSettled?: () => void;
}

function AgentTicker({ invoiceId, onSettled }: AgentTickerProps) {
  const {
    data: activity,
    error,
  } = useQuery({
    queryKey: queryKeys.invoiceActivity(invoiceId),
    queryFn: () => getActivity(invoiceId),
    // The stopping condition lives in the query rather than in a cleared
    // interval: returning false ends the polling, which is what the hand-rolled
    // version needed a `settled` flag, three clearInterval calls and a ref for.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && status !== "running" ? false : 1000;
    },
    // A finished transcript is immutable, so nothing re-reads it on age alone.
    // It is still re-read once when a decision invalidates the `invoices`
    // prefix, which reaches this key -- that is a real change, not staleness.
    staleTime: Infinity,
  });

  const settled = activity != null && activity.status !== null && activity.status !== "running";

  useEffect(() => {
    if (settled) onSettled?.();
    // `onSettled` is stable (the caller memoises it), so this fires on the
    // settle and not on every render of the page around it.
  }, [settled, onSettled]);

  if (error) {
    return <p className="error">{error instanceof Error ? error.message : "Something went wrong"}</p>;
  }

  // null status means the row does not exist yet -- the window between the
  // upload responding and run_agent's begin() actually committing -- and must
  // not be read as "finished with nothing to show".
  if (!activity || activity.status === null) {
    return <p className="ticker">Starting…</p>;
  }

  if (activity.status === "running") {
    return (
      <p className="ticker">
        {activity.latest ? describeStep(activity.latest.tool, activity.latest.input) : "Starting…"}
        {activity.call_count > 0 &&
          ` (${activity.call_count} step${activity.call_count === 1 ? "" : "s"} so far)`}
      </p>
    );
  }

  return (
    <p className="ticker">
      Done — {activity.decision ? `recommended ${activity.decision}` : "no decision reached"}
    </p>
  );
}

export default AgentTicker;
