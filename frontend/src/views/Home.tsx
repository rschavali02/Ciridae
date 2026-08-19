import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { listInvoices, uploadInvoice } from "../api";
import { queryKeys } from "../queryKeys";

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
  const [file, setFile] = useState<File | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: invoices, error: listError } = useQuery({
    queryKey: queryKeys.invoices,
    queryFn: listInvoices,
  });

  const upload = useMutation({
    mutationFn: uploadInvoice,
    onSuccess: (response) => {
      // The new invoice belongs in the queue behind this navigation.
      queryClient.invalidateQueries({ queryKey: queryKeys.invoices });
      // Straight to the invoice's own URL. The review has not finished, and
      // that route renders the live ticker until it does -- so the page a
      // reviewer watches is the page they can send to someone else.
      navigate(`/invoices/${response.id}`);
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (file) upload.mutate(file);
  }

  const error = upload.error ?? (invoices ? null : listError);

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
          <label htmlFor="invoice-pdf">Invoice PDF</label>
          <input
            id="invoice-pdf"
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button type="submit" disabled={!file || upload.isPending}>
            {upload.isPending ? "Uploading…" : "Review invoice"}
          </button>
        </form>
        {error && (
          <p className="error">
            {error instanceof Error ? error.message : "Something went wrong"}
          </p>
        )}
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
                    <Link className="vendor-link" to={`/invoices/${invoice.id}`}>
                      {invoice.vendor_name ?? "Unknown vendor"}
                    </Link>
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
    </main>
  );
}

export default Home;
