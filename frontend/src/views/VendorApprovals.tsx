import { useEffect, useState } from "react";
import { approveVendor, listPendingVendors, type PendingVendor } from "../api";

interface VendorApprovalsProps {
  onBack?: () => void;
}

function VendorApprovals({ onBack }: VendorApprovalsProps) {
  const [vendors, setVendors] = useState<PendingVendor[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [approvingId, setApprovingId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const pending = await listPendingVendors();
        if (cancelled) return;
        setVendors(pending);
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

  async function handleApprove(id: string) {
    setApprovingId(id);
    setActionError(null);
    try {
      await approveVendor(id);
      setVendors((prev) => (prev ? prev.filter((vendor) => vendor.id !== id) : prev));
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setApprovingId(null);
    }
  }

  if (error) {
    return <p className="error">{error}</p>;
  }

  if (!vendors) {
    return <p>Loading…</p>;
  }

  return (
    <main>
      {onBack && (
        <button type="button" onClick={onBack}>
          ← Back to queue
        </button>
      )}

      <h1>Vendor approvals</h1>

      <p>
        Approving a vendor makes it payable on this invoice and on every future invoice
        from that payee. Bank details below were taken from the invoice itself, not
        verified out of band — check them before approving.
      </p>

      {actionError && <p className="error">{actionError}</p>}

      {vendors.length === 0 ? (
        <p>Nothing waiting on you.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Bank details</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {vendors.map((vendor) => (
              <tr key={vendor.id}>
                <td>{vendor.name}</td>
                <td>
                  {vendor.bank_details ?? "—"} <span className="badge badge-vision">unverified</span>
                </td>
                <td>
                  <button
                    type="button"
                    onClick={() => handleApprove(vendor.id)}
                    disabled={approvingId !== null}
                  >
                    {approvingId === vendor.id ? "Approving…" : "Approve"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}

export default VendorApprovals;
