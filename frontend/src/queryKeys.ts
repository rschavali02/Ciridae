/**
 * Every cache key in one place, nested so that invalidating `invoices` reaches
 * the queue, every invoice and every activity feed in one call.
 */
export const queryKeys = {
  invoices: ["invoices"] as const,
  invoice: (id: string) => ["invoices", id] as const,
  invoiceActivity: (id: string) => ["invoices", id, "activity"] as const,
  auditLog: ["audit-log"] as const,
  pendingVendors: ["vendors", "pending"] as const,
};
