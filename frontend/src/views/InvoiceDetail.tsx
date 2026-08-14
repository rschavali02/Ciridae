import { useEffect, useState } from "react";
import { approveInvoice, getInvoice, rejectInvoice, type InvoiceDetail as InvoiceDetailType } from "../api";
import PdfPreview from "../components/PdfPreview";
import ReasoningSummary from "../components/ReasoningSummary";
import ToolCallTimeline from "../components/ToolCallTimeline";

interface InvoiceDetailProps {
  invoiceId: string;
  onBack?: () => void;
}

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null) return "—";
  return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
}

function formatConfidence(confidence: number | null): string {
  return confidence !== null ? `${Math.round(confidence * 100)}%` : "—";
}

function InvoiceDetail({ invoiceId, onBack }: InvoiceDetailProps) {
  const [invoice, setInvoice] = useState<InvoiceDetailType | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actingOn, setActingOn] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const detail = await getInvoice(invoiceId);
        if (cancelled) return;
        setInvoice(detail);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Something went wrong");
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [invoiceId]);

  async function handleDecision(action: "approve" | "reject") {
    setActingOn(action);
    setActionError(null);
    try {
      const summary = await (action === "approve" ? approveInvoice : rejectInvoice)(
        invoiceId,
        note
      );
      setInvoice((prev) => (prev ? { ...prev, ...summary } : prev));
      // Cleared on success, not on every keystroke: a failed attempt should
      // leave what was typed in place rather than making the reviewer retype
      // their reasoning after a transient error.
      setNote("");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setActingOn(null);
    }
  }

  if (error) {
    return <p className="error">{error}</p>;
  }

  if (!invoice) {
    return <p>Loading…</p>;
  }

  return (
    <main>
      {onBack && (
        <button type="button" onClick={onBack}>
          ← Back to queue
        </button>
      )}

      <h1>{invoice.vendor_name ?? "Unknown vendor"}</h1>

      <section className="modal-pdf">
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

      <section className="decision-note">
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
