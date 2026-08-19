import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { approveVendor, listPendingVendors } from "../api";
import { queryKeys } from "../queryKeys";

function VendorApprovals() {
  const queryClient = useQueryClient();

  // Same key the badge in App uses, so the two share a single read.
  const { data: vendors, error } = useQuery({
    queryKey: queryKeys.pendingVendors,
    queryFn: listPendingVendors,
  });

  const approve = useMutation({
    mutationFn: approveVendor,
    onSuccess: () => {
      // Two things went stale, not one. The vendor leaves this queue -- and
      // approving it also adopts the invoices that were waiting on that payee,
      // so the invoice queue behind this screen is stale too. Prefix matching
      // means one call per concern, and neither is the caller's job to
      // remember at the point they navigate away.
      queryClient.invalidateQueries({ queryKey: queryKeys.pendingVendors });
      queryClient.invalidateQueries({ queryKey: queryKeys.invoices });
    },
  });

  if (error && !vendors) {
    return <p className="error">{error instanceof Error ? error.message : "Something went wrong"}</p>;
  }

  if (!vendors) {
    return <p>Loading…</p>;
  }

  return (
    <main>
      <Link className="back-link" to="/">← Back to queue</Link>

      <h1>Vendor approvals</h1>

      <p>
        Approving a vendor makes it payable on this invoice and on every future invoice
        from that payee. Bank details below were taken from the invoice itself, not
        verified out of band — check them before approving.
      </p>

      {approve.error && (
        <p className="error">
          {approve.error instanceof Error ? approve.error.message : "Something went wrong"}
        </p>
      )}

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
                    onClick={() => approve.mutate(vendor.id)}
                    disabled={approve.isPending}
                  >
                    {approve.isPending && approve.variables === vendor.id
                      ? "Approving…"
                      : "Approve"}
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
