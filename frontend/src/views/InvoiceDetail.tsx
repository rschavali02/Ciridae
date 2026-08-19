import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import {
  approveInvoice,
  getInvoice,
  rejectInvoice,
  type InvoiceDetail as InvoiceDetailType,
} from "../api";
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
function InvoiceDetail() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  const navigate = useNavigate();
  const [invoice, setInvoice] = useState<InvoiceDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  // Bumped when the ticker sees the run settle, to pull the finished
  // transcript. This is a stand-in for a cache invalidation.
  const [reloadKey, setReloadKey] = useState(0);
  // A run whose backend died stays "running" in the database forever. Rather
  // than leave this URL as a ticker that never resolves, let the reviewer drop
  // to the fields that were extracted before the run stalled.
  const [showStalledDetail, setShowStalledDetail] = useState(false);

  useEffect(() => {
    if (!invoiceId) return;
    let cancelled = false;

    async function load() {
      try {
        const detail = await getInvoice(invoiceId!);
        if (cancelled) return;
        setInvoice(detail);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        // Only a first load has nothing to fall back on. A refresh that fails
        // keeps whatever was already on screen -- blanking a settled decision
        // because a background re-read 503'd loses the thing the reviewer
        // came for, and the ticker has already stopped polling by then.
        setInvoice((current) => {
          if (current === null) {
            setError(err instanceof Error ? err.message : "Something went wrong");
          }
          return current;
        });
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [invoiceId, reloadKey]);

  async function handleDecision(action: "approve" | "reject") {
    if (!invoiceId) return;
    setActingOn(action);
    setActionError(null);
    try {
      await (action === "approve" ? approveInvoice : rejectInvoice)(invoiceId, note);
      // Back to the queue rather than staying on the now-decided invoice.
      // Leaving it open kept Approve/Reject live with nothing to stop a second
      // click -- which is how the same invoice ended up with several identical
      // entries in the decision history.
      navigate("/");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Something went wrong");
      setActingOn(null);
    }
  }

  if (error) {
    return <p className="error">{error}</p>;
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
              onSettled={() => setReloadKey((key) => key + 1)}
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

      {actionError && <p className="error">{actionError}</p>}

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
          <button type="button" onClick={() => handleDecision("approve")} disabled={actingOn !== null}>
            {actingOn === "approve" ? "Approving…" : "Approve"}
          </button>
          <button type="button" onClick={() => handleDecision("reject")} disabled={actingOn !== null}>
            {actingOn === "reject" ? "Rejecting…" : "Reject"}
          </button>
        </div>
      </section>
    </main>
  );
}

export default InvoiceDetail;
