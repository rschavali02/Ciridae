import { useEffect, useState } from "react";
import { listPendingVendors } from "./api";
import History from "./views/History";
import Home from "./views/Home";
import VendorApprovals from "./views/VendorApprovals";
import "./App.css";

type Screen = "home" | "history" | "vendors";

function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [pendingVendorCount, setPendingVendorCount] = useState(0);

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

  if (screen === "vendors") {
    return <VendorApprovals onBack={() => setScreen("home")} />;
  }

  return (
    <>
      <nav className="tabs">
        <button
          type="button"
          className={screen === "home" ? "active" : ""}
          onClick={() => setScreen("home")}
        >
          Invoices
        </button>
        <button
          type="button"
          className={screen === "history" ? "active" : ""}
          onClick={() => setScreen("history")}
        >
          History
        </button>
      </nav>

      {pendingVendorCount > 0 && (
        <button type="button" className="vendor-badge" onClick={() => setScreen("vendors")}>
          {pendingVendorCount} vendor{pendingVendorCount === 1 ? "" : "s"} awaiting approval
        </button>
      )}

      {screen === "home" ? <Home /> : <History onBack={() => setScreen("home")} />}
    </>
  );
}

export default App;
