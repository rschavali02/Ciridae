import { QueryClient, type DefaultOptions } from "@tanstack/react-query";

/**
 * How long a read stays fresh before the *next mount or refocus* goes back to
 * the network. It is not a timer: a screen left open does not refresh itself
 * at this interval, or at any interval, unless it asks for one.
 *
 * Long enough that moving between the queue, an invoice and back does not
 * re-fetch the same list three times. Correctness does not rest on it --
 * everything that makes the queue wrong (a decision, a vendor approval, a run
 * settling) invalidates explicitly and is re-read regardless of age.
 */
export const STALE_TIME_MS = 30_000;

/** Shared by the app and by tests, so the caching under test is what ships. */
export function createQueryClient(queries: DefaultOptions["queries"] = {}) {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: STALE_TIME_MS, ...queries } },
  });
}
