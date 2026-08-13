import { useEffect, useState } from "react";
import { listInvoices, listPendingVendors, type InvoiceSummary } from "../api";

interface QueueProps {
  onViewPendingVendors?: () => void;
}

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null) return "—";
  return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
}

function oneLineReason(reasoning: string | null): string {
  if (!reasoning) return "—";
  return reasoning.split(/(?<=[.!?])\s/)[0];
}

function InvoiceRow({ invoice }: { invoice: InvoiceSummary }) {
  return (
    <tr>
      <td>{invoice.vendor_name ?? "Unknown vendor"}</td>
      <td>{formatAmount(invoice.amount, invoice.currency)}</td>
      <td>{invoice.decision ?? "—"}</td>
      <td>
        {invoice.confidence !== null ? `${Math.round(invoice.confidence * 100)}%` : "—"}
      </td>
      <td>{oneLineReason(invoice.reasoning)}</td>
    </tr>
  );
}

function InvoiceTable({ invoices, empty }: { invoices: InvoiceSummary[]; empty: string }) {
  if (invoices.length === 0) {
    return <p>{empty}</p>;
  }
  return (
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
        {invoices.map((invoice) => (
          <InvoiceRow key={invoice.id} invoice={invoice} />
        ))}
      </tbody>
    </table>
  );
}

function Queue({ onViewPendingVendors }: QueueProps) {
  const [invoices, setInvoices] = useState<InvoiceSummary[] | null>(null);
  const [pendingVendorCount, setPendingVendorCount] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [invoiceList, pendingVendors] = await Promise.all([
          listInvoices(),
          listPendingVendors(),
        ]);
        if (cancelled) return;
        setInvoices(invoiceList);
        setPendingVendorCount(pendingVendors.length);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Something went wrong");
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) {
    return <p className="error">{error}</p>;
  }

  if (!invoices) {
    return <p>Loading…</p>;
  }

  const cleared = invoices.filter((i) => i.decision === "approve");
  const needsYou = invoices.filter((i) => i.decision === "escalate" || i.decision === "reject");
  const total = cleared.length + needsYou.length;

  return (
    <main>
      <h1>Invoice queue</h1>

      <p>
        {cleared.length} of {total} cleared without review
      </p>

      {pendingVendorCount > 0 && (
        <button className="badge badge-vision" onClick={onViewPendingVendors}>
          {pendingVendorCount} vendor{pendingVendorCount === 1 ? "" : "s"} awaiting approval
        </button>
      )}

      <section>
        <h2>Cleared for payment</h2>
        <InvoiceTable invoices={cleared} empty="None yet." />
      </section>

      <section>
        <h2>Needs you</h2>
        <InvoiceTable invoices={needsYou} empty="Nothing waiting on you." />
      </section>
    </main>
  );
}

export default Queue;
