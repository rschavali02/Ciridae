import { useState } from "react";
import { extractInvoice, type ExtractResponse } from "./api";
import "./App.css";

function App() {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ExtractResponse | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await extractInvoice(file);
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main>
      <h1>Invoice Extraction</h1>
      <p>Upload a messy invoice PDF and get back structured fields.</p>

      <form onSubmit={handleSubmit}>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button type="submit" disabled={!file || loading}>
          {loading ? "Extracting…" : "Extract"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {result && (
        <section className="result">
          <p>
            <span className={`badge ${result.used_vision_fallback ? "badge-vision" : "badge-text"}`}>
              {result.used_vision_fallback ? "vision fallback used" : "text-layer extraction"}
            </span>
          </p>

          <table>
            <tbody>
              <tr>
                <th>Vendor</th>
                <td>{result.fields.vendor_name ?? "—"}</td>
              </tr>
              <tr>
                <th>Invoice #</th>
                <td>{result.fields.invoice_number ?? "—"}</td>
              </tr>
              <tr>
                <th>Amount</th>
                <td>{result.fields.amount != null ? `$${result.fields.amount.toFixed(2)}` : "—"}</td>
              </tr>
              <tr>
                <th>Due date</th>
                <td>{result.fields.due_date ?? "—"}</td>
              </tr>
              <tr>
                <th>PO number</th>
                <td>{result.fields.po_number ?? "—"}</td>
              </tr>
            </tbody>
          </table>

          <h2>Line items</h2>
          {result.fields.line_items.length === 0 ? (
            <p>None found.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Description</th>
                  <th>Amount</th>
                </tr>
              </thead>
              <tbody>
                {result.fields.line_items.map((item, i) => (
                  <tr key={i}>
                    <td>{item.description}</td>
                    <td>${item.amount.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <details>
            <summary>Raw extracted text</summary>
            <pre>{result.raw_text}</pre>
          </details>
        </section>
      )}
    </main>
  );
}

export default App;
