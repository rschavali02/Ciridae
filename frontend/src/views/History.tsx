import { useQuery } from "@tanstack/react-query";
import { listAuditLog } from "../api";
import { queryKeys } from "../queryKeys";

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

function History() {
  const { data: entries, error } = useQuery({
    queryKey: queryKeys.auditLog,
    queryFn: listAuditLog,
  });

  // Only a first read has nothing to fall back on; a failed refresh keeps the
  // log that is already on screen.
  if (error && !entries) {
    return <p className="error">{error instanceof Error ? error.message : "Something went wrong"}</p>;
  }

  if (!entries) {
    return <p>Loading…</p>;
  }

  return (
    <main className="home-queue">
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
