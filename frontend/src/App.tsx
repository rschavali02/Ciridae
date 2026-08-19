import { useQuery } from "@tanstack/react-query";
import { Link, NavLink, Outlet, useMatch } from "react-router";
import { listPendingVendors } from "./api";
import { queryKeys } from "./queryKeys";
import "./App.css";

/** The layout every screen renders inside: nav, vendor badge, and the route. */
function App() {
  // Route matching rather than string comparison: `/vendors/` resolves to the
  // same route, and a bare `pathname !== "/vendors"` would miss it and render
  // a badge on the page it links to.
  const onVendors = useMatch("/vendors");
  // An invoice's own page belongs to the Invoices section, so the tab stays
  // lit there. NavLink's own `isActive` is exact-match on "/" and would leave
  // the whole nav dark on the screen reviewers sit on longest.
  //
  // Both matchers are called unconditionally: `a ?? b` would skip the second
  // hook whenever the first matched, which is a rules-of-hooks violation that
  // crashes the tree on the very navigation it is meant to describe.
  const atQueue = useMatch("/");
  const atInvoice = useMatch("/invoices/*");
  const onInvoices = Boolean(atQueue || atInvoice);

  // Polled rather than refreshed only on invoice completion: a draft can also
  // be created by an invoice uploaded earlier in the same session, and the
  // badge should reflect the queue even if nothing new has just finished.
  //
  // Same key as the vendor approvals screen, so the two share one read rather
  // than each fetching the list independently. A failed poll keeps the last
  // known count instead of interrupting whatever the reviewer is doing.
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
