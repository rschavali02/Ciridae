/**
 * Every cache key in one place, because key design *is* invalidation design:
 * `invalidateQueries` matches by prefix, so the nesting below is what decides
 * what a mutation refreshes.
 *
 * `invoices` is the prefix of both `invoice(id)` and `invoiceActivity(id)`, so
 * invalidating it reaches the queue, every invoice and every activity feed in
 * one call. Note the direction: `["invoices", id]` does NOT reach `["invoices"]`,
 * which is why anything that changes a queue row invalidates the bare prefix.
 */
export const queryKeys = {
  invoices: ["invoices"] as const,
  invoice: (id: string) => ["invoices", id] as const,
  invoiceActivity: (id: string) => ["invoices", id, "activity"] as const,
  auditLog: ["audit-log"] as const,
  pendingVendors: ["vendors", "pending"] as const,
};
