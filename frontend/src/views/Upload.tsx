import { useState } from "react";
import { uploadInvoice } from "../api";
import AgentTicker from "../components/AgentTicker";

function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invoiceId, setInvoiceId] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;

    setUploading(true);
    setError(null);
    setInvoiceId(null);

    try {
      const response = await uploadInvoice(file);
      setInvoiceId(response.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setUploading(false);
    }
  }

  return (
    <main>
      <h1>Upload an invoice</h1>
      <p>Upload an invoice PDF and watch the agent work in near real time.</p>

      <form onSubmit={handleSubmit}>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button type="submit" disabled={!file || uploading}>
          {uploading ? "Uploading…" : "Upload"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {invoiceId && (
        <section className="result">
          <AgentTicker key={invoiceId} invoiceId={invoiceId} />
        </section>
      )}
    </main>
  );
}

export default Upload;
