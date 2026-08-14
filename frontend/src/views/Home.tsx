import { useCallback, useEffect, useState } from "react";
import { listInvoices, uploadInvoice, type InvoiceSummary } from "../api";
import AgentTicker from "../components/AgentTicker";
import PdfPreview from "../components/PdfPreview";
import InvoiceDetail from "./InvoiceDetail";

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null) return "—";
  return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
}

function oneLineReason(reasoning: string | null): string {
  if (!reasoning) return "—";
  return reasoning.split(/(?<=[.!?])\s/)[0];
}

// Approve reads as blue, escalate/reject as red -- the two outcomes that
// actually need a human are visually one color, matched to the badge palette
// InvoiceDetail and VendorApprovals already use for "needs attention".
function decisionRowClass(decision: string | null): string {
  if (decision === "approve") return "row-approved";
  if (decision === "escalate" || decision === "reject") return "row-held";
  return "";
}

function Home() {
  const [invoices, setInvoices] = useState<InvoiceSummary[] | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [processingId, setProcessingId] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setInvoices(await listInvoices());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setUploading(true);
    setError(null);
    try {
      const response = await uploadInvoice(file);
      // Switches the page into the split-screen state; the uploader and any
      // existing table are unmounted, not merely hidden, while this is set.
      setProcessingId(response.id);
      setFile(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setUploading(false);
    }
  }

  function handleSettled() {
    setProcessingId(null);
    refresh();
  }

  function closeDetail() {
    // InvoiceDetail updates its own local state optimistically after an
    // approve/reject, but this table's rows do not share that state -- without
    // a refresh here, a row approved from the modal would keep showing its old
    // decision until something else happened to reload the list.
    setSelectedId(null);
    refresh();
  }

  if (processingId) {
    return (
      <main className="home-processing">
        <div className="processing-split">
          <div className="preview-pane">
            <PdfPreview invoiceId={processingId} />
          </div>
          <div className="ticker-pane">
            <h2>Reviewing invoice</h2>
            <AgentTicker key={processingId} invoiceId={processingId} onSettled={handleSettled} />
          </div>
        </div>
      </main>
    );
  }

  // Once a person has approved or rejected an invoice, it belongs in History,
  // not here -- otherwise a decided invoice keeps sitting in the queue that is
  // supposed to be "what still needs you", indistinguishable from one nobody
  // has looked at yet. Still-running and still-pending reviews stay: only a
  // human decision moves status off "pending".
  const openInvoices = (invoices ?? []).filter((invoice) => invoice.status === "pending");
  const hasInvoices = openInvoices.length > 0;

  return (
    <main className={hasInvoices ? "home-queue" : "home-idle"}>
      <section className="uploader">
        <h1>{hasInvoices ? "Review another invoice" : "Upload an invoice"}</h1>
        {!hasInvoices && (
          <p>Upload an invoice PDF and watch the agent work in near real time.</p>
        )}
        <form onSubmit={handleSubmit}>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button type="submit" disabled={!file || uploading}>
            {uploading ? "Uploading…" : "Review invoice"}
          </button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>

      {hasInvoices && (
        <section className="spreadsheet">
          <table>
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Amount</th>
                <th>Decision</th>
                <th>Confidence</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {openInvoices.map((invoice) => (
                <tr key={invoice.id} className={decisionRowClass(invoice.decision)}>
                  <td>
                    <button
                      type="button"
                      className="vendor-link"
                      onClick={() => setSelectedId(invoice.id)}
                    >
                      {invoice.vendor_name ?? "Unknown vendor"}
                    </button>
                  </td>
                  <td>{formatAmount(invoice.amount, invoice.currency)}</td>
                  <td>
                    {invoice.decision ?? (invoice.run_status === "running" ? "reviewing…" : "—")}
                  </td>
                  <td>
                    {invoice.confidence !== null ? `${Math.round(invoice.confidence * 100)}%` : "—"}
                  </td>
                  <td>{oneLineReason(invoice.reasoning)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {selectedId && (
        <div className="modal-overlay" onClick={closeDetail}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <InvoiceDetail invoiceId={selectedId} onBack={closeDetail} />
          </div>
        </div>
      )}
    </main>
  );
}

export default Home;
