const API_BASE = "http://localhost:8000";

export interface ToolCall {
  tool: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
}

export interface PolicyClause {
  section: string;
  text: string;
}

export interface InvoiceSummary {
  id: string;
  invoice_number: string | null;
  vendor_name: string | null;
  amount: number | null;
  currency: string | null;
  status: string;
  created_at: string | null;
  run_status: string | null;
  decision: string | null;
  confidence: number | null;
  reasoning: string | null;
}

export interface InvoiceDetail extends InvoiceSummary {
  due_date: string | null;
  po_number: string | null;
  tool_calls: ToolCall[];
  policy_clauses: PolicyClause[];
}

export interface Activity {
  status: string | null;
  latest: { tool: string; input: Record<string, unknown> } | null;
  call_count: number;
  decision: string | null;
}

export interface PendingVendor {
  id: string;
  name: string;
  bank_details: string | null;
}

export interface AuditEntry {
  id: string;
  invoice_id: string;
  vendor_name: string | null;
  amount: number | null;
  currency: string | null;
  action: string;
  note: string | null;
  agent_decision: string | null;
  decided_at: string | null;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function uploadInvoice(file: File): Promise<{ id: string; status: string }> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(`${API_BASE}/invoices`, {
    method: "POST",
    body: formData,
  });

  return handleResponse(response);
}

export async function listInvoices(): Promise<InvoiceSummary[]> {
  const response = await fetch(`${API_BASE}/invoices`);
  return handleResponse(response);
}

export async function getInvoice(id: string): Promise<InvoiceDetail> {
  const response = await fetch(`${API_BASE}/invoices/${id}`);
  return handleResponse(response);
}

export async function getActivity(id: string): Promise<Activity> {
  const response = await fetch(`${API_BASE}/invoices/${id}/activity`);
  return handleResponse(response);
}

async function decide(id: string, action: "approve" | "reject", note: string): Promise<InvoiceSummary> {
  const trimmed = note.trim();
  const response = await fetch(`${API_BASE}/invoices/${id}/${action}`, {
    method: "POST",
    // Omitted entirely rather than sent as {note: ""}: the backend already
    // treats a missing body as "no note", so an empty string would just be a
    // second, redundant way of saying the same thing.
    ...(trimmed && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: trimmed }),
    }),
  });
  return handleResponse(response);
}

export async function approveInvoice(id: string, note = ""): Promise<InvoiceSummary> {
  return decide(id, "approve", note);
}

export async function rejectInvoice(id: string, note = ""): Promise<InvoiceSummary> {
  return decide(id, "reject", note);
}

export async function listAuditLog(): Promise<AuditEntry[]> {
  const response = await fetch(`${API_BASE}/audit-log`);
  return handleResponse(response);
}

export async function listPendingVendors(): Promise<PendingVendor[]> {
  const response = await fetch(`${API_BASE}/vendors/pending`);
  return handleResponse(response);
}

export function invoiceFileUrl(id: string): string {
  return `${API_BASE}/invoices/${id}/file`;
}

export async function approveVendor(
  id: string
): Promise<{ id: string; name: string; approval_status: string }> {
  const response = await fetch(`${API_BASE}/vendors/${id}/approve`, { method: "POST" });
  return handleResponse(response);
}
