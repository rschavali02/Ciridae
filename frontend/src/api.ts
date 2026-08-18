import type { components } from "./api-types";

const API_BASE = "http://localhost:8000";

// Generated from the backend's OpenAPI schema -- run `npm run gen:types` after
// changing a response model. Do not hand-edit api-types.ts.
type Schemas = components["schemas"];

export type ToolCall = Schemas["ToolCall"];
export type PolicyClause = Schemas["PolicyClause"];
export type InvoiceSummary = Schemas["InvoiceSummary"];
export type InvoiceDetail = Schemas["InvoiceDetail"];
export type Activity = Schemas["Activity"];
export type PendingVendor = Schemas["PendingVendor"];
export type AuditEntry = Schemas["AuditEntry"];
export type VendorApproved = Schemas["VendorApproved"];
export type InvoiceAccepted = Schemas["InvoiceAccepted"];

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function uploadInvoice(file: File): Promise<InvoiceAccepted> {
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

export async function approveVendor(id: string): Promise<VendorApproved> {
  const response = await fetch(`${API_BASE}/vendors/${id}/approve`, { method: "POST" });
  return handleResponse(response);
}
