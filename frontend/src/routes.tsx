import type { RouteObject } from "react-router";
import { useParams } from "react-router";

import App from "./App";
import History from "./views/History";
import Home from "./views/Home";
import InvoiceDetail from "./views/InvoiceDetail";
import NotFound from "./views/NotFound";
import VendorApprovals from "./views/VendorApprovals";

/**
 * Narrows the URL param once and keys on it, so the view below can treat the
 * id as a plain string and a move between invoices remounts rather than
 * showing one invoice's transcript under another one's id.
 */
function KeyedInvoiceDetail() {
  const { invoiceId } = useParams<{ invoiceId: string }>();
  if (!invoiceId) return <NotFound />;
  return <InvoiceDetail key={invoiceId} invoiceId={invoiceId} />;
}

// Exported as data rather than built inside main.tsx so tests can mount the
// same table through createMemoryRouter -- the routing under test is the
// routing that ships.
export const routes: RouteObject[] = [
  {
    path: "/",
    element: <App />,
    // A render throw lands here rather than on React Router's bare framework
    // screen, which has no chrome and no way home.
    errorElement: <NotFound />,
    children: [
      { index: true, element: <Home /> },
      { path: "invoices/:invoiceId", element: <KeyedInvoiceDetail /> },
      { path: "history", element: <History /> },
      { path: "vendors", element: <VendorApprovals /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
