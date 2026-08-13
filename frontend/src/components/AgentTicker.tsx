import { useEffect, useState } from "react";
import { getActivity, type Activity } from "../api";

const LABELS: Record<string, (input: any) => string> = {
  lookup_vendor: (i) => `Resolving vendor "${i.vendor_name}"…`,
  get_invoice_history: () => "Checking payment history…",
  check_duplicate_invoice: () => "Checking for duplicates…",
  get_purchase_order: (i) => `Looking up ${i.po_number}…`,
  search_policy: (i) => `Searching policy: "${i.query}"…`,
  draft_vendor: (i) => `Drafting new vendor "${i.vendor_name}" for approval…`,
  submit_recommendation: () => "Reaching a decision…",
};

function describeStep(tool: string, input: Record<string, unknown>): string {
  const label = LABELS[tool];
  return label ? label(input) : `Running ${tool}…`;
}

interface AgentTickerProps {
  invoiceId: string;
}

function AgentTicker({ invoiceId }: AgentTickerProps) {
  const [activity, setActivity] = useState<Activity | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | undefined;

    async function poll() {
      try {
        const next = await getActivity(invoiceId);
        if (cancelled) return;
        setActivity(next);
        if (next.status !== "running" && intervalId !== undefined) {
          clearInterval(intervalId);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Something went wrong");
        if (intervalId !== undefined) clearInterval(intervalId);
      }
    }

    poll();
    intervalId = setInterval(poll, 1000);

    return () => {
      cancelled = true;
      if (intervalId !== undefined) clearInterval(intervalId);
    };
  }, [invoiceId]);

  if (error) {
    return <p className="error">{error}</p>;
  }

  if (!activity) {
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
      Done — {activity.decision ? `recommended ${activity.decision}` : activity.status}
    </p>
  );
}

export default AgentTicker;
