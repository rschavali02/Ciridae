import type {
  Activity,
  AuditEntry,
  InvoiceDetail,
  InvoiceSummary,
  PendingVendor,
} from "../api";

/** A review that has finished: the agent committed, so the detail view renders. */
export const settledSummary: InvoiceSummary = {
  id: "inv-settled",
  invoice_number: "INV-1001",
  vendor_name: "ACME Incorporated",
  amount: 500,
  currency: "USD",
  status: "pending",
  created_at: "2026-08-01T10:00:00Z",
  run_status: "complete",
  decision: "escalate",
  confidence: 0.62,
  reasoning: "No purchase order was referenced.",
};

export const settledDetail: InvoiceDetail = {
  ...settledSummary,
  due_date: "2026-09-01",
  po_number: null,
  tool_calls: [
    { tool: "lookup_vendor", input: { vendor_name: "Acme Inc" }, output: { match: "resolved" } },
  ],
  policy_clauses: [{ section: "III. Procedures", text: "Invoices without a purchase order..." }],
};

/** A review still in flight: no decision yet, so the ticker renders instead. */
export const runningSummary: InvoiceSummary = {
  id: "inv-running",
  invoice_number: null,
  vendor_name: "Globex Corporation",
  amount: 2400,
  currency: "USD",
  status: "pending",
  created_at: "2026-08-01T11:00:00Z",
  run_status: "running",
  decision: null,
  confidence: null,
  reasoning: null,
};

export const runningDetail: InvoiceDetail = {
  ...runningSummary,
  due_date: null,
  po_number: "PO-77",
  tool_calls: [],
  policy_clauses: [],
};

export const runningActivity: Activity = {
  status: "running",
  latest: { tool: "search_policy", input: { query: "purchase order threshold" } },
  call_count: 3,
  decision: null,
};

export const settledActivity: Activity = {
  status: "complete",
  latest: { tool: "submit_recommendation", input: { decision: "escalate" } },
  call_count: 6,
  decision: "escalate",
};

export const pendingVendor: PendingVendor = {
  id: "vendor-pending",
  name: "Initech LLC",
  bank_details: "IBAN GB33BUKB20201555555555",
};

export const auditEntry: AuditEntry = {
  id: "audit-1",
  invoice_id: "inv-settled",
  vendor_name: "ACME Incorporated",
  amount: 500,
  currency: "USD",
  action: "approve",
  note: "Checked against the PO by hand.",
  agent_decision: "escalate",
  decided_at: "2026-08-02T09:30:00Z",
};
