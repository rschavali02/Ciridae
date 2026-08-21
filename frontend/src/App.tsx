import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useMatch } from "react-router";
import { listPendingVendors } from "./api";
import { queryKeys } from "./queryKeys";
import "./App.css";

/** The layout every screen renders inside: nav, vendor badge, and the route. */
function App() {
  const onVendors = useMatch("/vendors");
  const atQueue = useMatch("/");
  const atInvoice = useMatch("/invoices/*");
  const onInvoices = Boolean(atQueue || atInvoice);

  // Polled rather than refreshed only on invoice completion
  const { data: pendingVendors } = useQuery({
    queryKey: queryKeys.pendingVendors,
    queryFn: listPendingVendors,
    refetchInterval: 5000,
  });

  const pendingVendorCount = pendingVendors?.length ?? 0;
  const showBadge = pendingVendorCount > 0 && !onVendors;

  return (
    <>
      <nav className="tabs">
        <NavLink to="/" className={() => (onInvoices ? "active" : "")}>
          Invoices
        </NavLink>
        <NavLink to="/history" className={({ isActive }) => (isActive ? "active" : "")}>
          History
        </NavLink>
      </nav>

      {showBadge && (
        <Link to="/vendors" className="vendor-badge">
          {pendingVendorCount} vendor{pendingVendorCount === 1 ? "" : "s"} awaiting approval
        </Link>
      )}

      <Outlet />
    </>
  );
}

export default App;
