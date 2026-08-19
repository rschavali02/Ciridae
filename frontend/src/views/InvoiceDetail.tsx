import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router";
import { approveInvoice, getInvoice, rejectInvoice } from "../api";
import { queryKeys } from "../queryKeys";
import AgentTicker from "../components/AgentTicker";
import PdfPreview from "../components/PdfPreview";
import ReasoningSummary from "../components/ReasoningSummary";
import ToolCallTimeline from "../components/ToolCallTimeline";

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null) return "—";
  return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
}

function formatConfidence(confidence: number | null): string {
  return confidence !== null ? `${Math.round(confidence * 100)}%` : "—";
}

/**
 * One invoice, at whatever stage its review has reached.
 *
 * A run still in flight and a run that has settled are the same URL on purpose:
 * a reviewer can hand someone the link to a review that is still happening, and
 * the page becomes the decision when the agent reaches one.
 */
interface InvoiceDetailProps {
  /** Narrowed by the route wrapper, so nothing below has to re-check it. */
  invoiceId: string;
}

function InvoiceDetail({ invoiceId }: InvoiceDetailProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  // A run whose backend died stays "running" in the database forever. Rather
  // than leave this URL as a ticker that never resolves, let the reviewer drop
  // to the fields that were extracted before the run stalled.
  const [showStalledDetail, setShowStalledDetail] = useState(false);

  const { data: invoice, error } = useQuery({
    queryKey: queryKeys.invoice(invoiceId),
    queryFn: () => getInvoice(invoiceId),
  });

  // Memoised so the ticker's settle effect fires on the settle rather than on
  // every render of this page.
  const handleSettled = useCallback(() => {
    // The whole `invoices` prefix, not just this one. Prefix matching runs in
    // one direction: `["invoices", id]` reaches this invoice's activity feed
    // but never the queue above it. A run settling changes the queue row's
    // decision, confidence and run_status, so the queue is exactly what went
    // stale -- and nothing else would re-read it, since it has no interval and
    // a back navigation fires no focus event.
    queryClient.invalidateQueries({ queryKey: queryKeys.invoices });
  }, [queryClient]);

  const decide = useMutation({
    mutationFn: ({ action }: { action: "approve" | "reject" }) =>
      (action === "approve" ? approveInvoice : rejectInvoice)(invoiceId, note),
    onSuccess: () => {
      // One call covers the queue, this invoice and its activity feed -- they
      // share the `invoices` prefix -- plus the audit log the decision just
      // wrote a row to.
      queryClient.invalidateQueries({ queryKey: queryKeys.invoices });
      queryClient.invalidateQueries({ queryKey: queryKeys.auditLog });
      // Back to the queue rather than staying on the now-decided invoice.
      // Leaving it open kept Approve/Reject live with nothing to stop a second
      // click -- which is how the same invoice ended up with several identical
      // entries in the decision history.
      navigate("/");
    },
  });

  if (error && !invoice) {
    return (
      <p className="error">{error instanceof Error ? error.message : "Something went wrong"}</p>
    );
  }

  if (!invoice) {
    return <p>Loading…</p>;
  }

  // Anything short of a completed run has no decision to show, only progress.
  // `null` counts: it is the window between the upload responding and the
  // agent's first write, and it must not read as "finished with nothing".
  if (invoice.run_status !== "complete" && !showStalledDetail) {
    return (
      <main className="invoice-review">
        <Link className="back-link" to="/">← Back to queue</Link>
        <div className="processing-split">
          <div className="preview-pane">
            <PdfPreview invoiceId={invoice.id} />
          </div>
          <div className="ticker-pane">
            <h2>Reviewing invoice</h2>
            <AgentTicker
              key={invoice.id}
              invoiceId={invoice.id}
              onSettled={handleSettled}
            />
            <button type="button" onClick={() => setShowStalledDetail(true)}>
              Show details anyway
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main>
      <Link className="back-link" to="/">← Back to queue</Link>

      <h1>{invoice.vendor_name ?? "Unknown vendor"}</h1>

      <section className="detail-pdf">
        <PdfPreview invoiceId={invoice.id} />
      </section>

      <section>
        <table>
          <tbody>
            <tr>
              <th>Invoice number</th>
              <td>{invoice.invoice_number ?? "—"}</td>
            </tr>
            <tr>
              <th>Amount</th>
              <td>{formatAmount(invoice.amount, invoice.currency)}</td>
            </tr>
            <tr>
              <th>Due date</th>
              <td>{invoice.due_date ?? "—"}</td>
            </tr>
            <tr>
              <th>PO number</th>
              <td>{invoice.po_number ?? "—"}</td>
            </tr>
            <tr>
              <th>Status</th>
              <td>{invoice.status}</td>
            </tr>
            <tr>
              <th>Decision</th>
              <td>{invoice.decision ?? "—"}</td>
            </tr>
            <tr>
              <th>Confidence</th>
              <td>{formatConfidence(invoice.confidence)}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2>Reasoning</h2>
        <ReasoningSummary reasoning={invoice.reasoning} />
      </section>

      <section>
        <h2>Policy clauses cited</h2>
        {invoice.policy_clauses.length === 0 ? (
          <p>None cited.</p>
        ) : (
          <ul>
            {invoice.policy_clauses.map((clause, index) => (
              <li key={index}>
                <strong>{clause.section}</strong>
                <p>{clause.text}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>Tool call timeline</h2>
        <ToolCallTimeline toolCalls={invoice.tool_calls} />
      </section>

      {decide.error && (
        <p className="error">
          {decide.error instanceof Error ? decide.error.message : "Something went wrong"}
        </p>
      )}

      <section className="decision-note" hidden={invoice.run_status !== "complete"}>
        <label htmlFor="decision-note">Note (optional)</label>
        <textarea
          id="decision-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why you're approving or rejecting this -- goes on the audit record."
          rows={3}
        />
        <div>
          <button
            type="button"
            onClick={() => decide.mutate({ action: "approve" })}
            disabled={decide.isPending}
          >
            {decide.isPending && decide.variables?.action === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={() => decide.mutate({ action: "reject" })}
            disabled={decide.isPending}
          >
            {decide.isPending && decide.variables?.action === "reject" ? "Rejecting…" : "Reject"}
          </button>
        </div>
      </section>
    </main>
  );
}

export default InvoiceDetail;
