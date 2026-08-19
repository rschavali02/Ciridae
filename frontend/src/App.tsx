import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useMatch } from "react-router";
import { listPendingVendors } from "./api";
import "./App.css";

/** The layout every screen renders inside: nav, vendor badge, and the route. */
function App() {
  const [pendingVendorCount, setPendingVendorCount] = useState(0);
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
  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const pending = await listPendingVendors();
        if (!cancelled) setPendingVendorCount(pending.length);
      } catch {
        // A failed background poll should not interrupt whatever the user is
        // doing -- the badge just stays at its last known count.
      }
    }

    poll();
    const intervalId = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, []);

  // Hidden on the vendors screen itself: a count of what you are already
  // looking at is noise, and the link would lead back to this page.
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
