import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library does not auto-cleanup when `globals` is on in some setups;
// doing it explicitly keeps one test's DOM out of the next one's queries.
afterEach(cleanup);
