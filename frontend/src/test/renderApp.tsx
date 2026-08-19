import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router";

import { createQueryClient } from "../queryClient";
import { routes } from "../routes";

/**
 * Mounts the real route table and the real query configuration at a given URL.
 *
 * A fresh client per call, so one test's cache can never answer another test's
 * question. Retries are off: they are a production behaviour, and leaving them
 * on would make every deliberate failure take seconds to surface.
 */
export function renderApp(path: string) {
  const queryClient = createQueryClient({ retry: false });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
