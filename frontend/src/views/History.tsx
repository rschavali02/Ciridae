import { useEffect, useState } from "react";
import { listAuditLog, type AuditEntry } from "../api";

function formatAmount(amount: number | null, currency: string | null): string {
  if (amount === null) return "—";
  return `${currency ?? ""} ${amount.toFixed(2)}`.trim();
}

function formatWhen(decidedAt: string | null): string {
  if (!decidedAt) return "—";
  return new Date(decidedAt).toLocaleString();
}

function actionClass(action: string): string {
  return action === "approve" ? "row-approved" : "row-held";
}

interface HistoryProps {
  onBack?: () => void;
}

function History({ onBack }: HistoryProps) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const data = await listAuditLog();
        if (!cancelled) setEntries(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Something went wrong");
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

  if (!entries) {
    return <p>Loading…</p>;
  }

  return (
    <main className="home-queue">
      {onBack && (
        <button type="button" onClick={onBack}>
          ← Back
        </button>
      )}

      <h1>Decision history</h1>
      <p>Every invoice a person has approved or rejected, most recent first.</p>

      {entries.length === 0 ? (
        <p>No decisions recorded yet.</p>
      ) : (
        <section className="spreadsheet">
          <table>
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Amount</th>
                <th>Decision</th>
                <th>Agent had recommended</th>
                <th>Note</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id} className={actionClass(entry.action)}>
                  <td>{entry.vendor_name ?? "Unknown vendor"}</td>
                  <td>{formatAmount(entry.amount, entry.currency)}</td>
                  <td>{entry.action === "approve" ? "Approved" : "Rejected"}</td>
                  <td>{entry.agent_decision ?? "—"}</td>
                  <td>{entry.note ?? "—"}</td>
                  <td>{formatWhen(entry.decided_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}

export default History;
