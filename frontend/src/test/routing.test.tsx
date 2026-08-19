import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, test, vi } from "vitest";

import * as api from "../api";
import { routes } from "../routes";
import {
  auditEntry,
  pendingVendor,
  runningActivity,
  runningDetail,
  settledActivity,
  settledDetail,
  settledSummary,
} from "./fixtures";

// The network is the one thing that has to be faked -- every other piece under
// test (the route table, the views, the navigation) is the real code.
vi.mock("../api", () => ({
  listInvoices: vi.fn(),
  getInvoice: vi.fn(),
  getActivity: vi.fn(),
  listAuditLog: vi.fn(),
  listPendingVendors: vi.fn(),
  uploadInvoice: vi.fn(),
  approveInvoice: vi.fn(),
  rejectInvoice: vi.fn(),
  approveVendor: vi.fn(),
  invoiceFileUrl: (id: string) => `http://localhost:8000/invoices/${id}/file`,
}));

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(<RouterProvider router={router} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  // Sensible empty defaults; individual tests override what they care about.
  vi.mocked(api.listInvoices).mockResolvedValue([]);
  vi.mocked(api.listAuditLog).mockResolvedValue([]);
  vi.mocked(api.listPendingVendors).mockResolvedValue([]);
});

describe("each screen has its own URL", () => {
  test("the index route shows the uploader", async () => {
    renderAt("/");
    expect(
      await screen.findByRole("heading", { name: /upload an invoice/i }),
    ).toBeInTheDocument();
  });

  test("/history shows the decision history", async () => {
    renderAt("/history");
    expect(
      await screen.findByRole("heading", { name: /decision history/i }),
    ).toBeInTheDocument();
  });

  test("/vendors shows the vendor approvals queue", async () => {
    renderAt("/vendors");
    expect(
      await screen.findByRole("heading", { name: /vendor approvals/i }),
    ).toBeInTheDocument();
  });

  test("/invoices/:invoiceId shows that invoice, read from the URL", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);

    renderAt("/invoices/inv-settled");

    expect(
      await screen.findByRole("heading", { name: "ACME Incorporated" }),
    ).toBeInTheDocument();
    expect(screen.getByText("INV-1001")).toBeInTheDocument();
    expect(api.getInvoice).toHaveBeenCalledWith("inv-settled");
  });
});

describe("one invoice URL covers the whole review", () => {
  test("shows the live ticker while the agent is still working", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(runningActivity);

    renderAt("/invoices/inv-running");

    expect(await screen.findByText(/searching policy/i)).toBeInTheDocument();
    // The settled view must not be on screen: there is no decision yet.
    expect(screen.queryByText(/tool call timeline/i)).not.toBeInTheDocument();
  });

  test("shows the decision once the run has settled", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);

    renderAt("/invoices/inv-settled");

    expect(await screen.findByText(/tool call timeline/i)).toBeInTheDocument();
    expect(screen.queryByText(/searching policy/i)).not.toBeInTheDocument();
  });
});

describe("navigation", () => {
  test("clicking a vendor in the queue opens that invoice's URL", async () => {
    vi.mocked(api.listInvoices).mockResolvedValue([settledSummary]);
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);
    const user = userEvent.setup();

    renderAt("/");
    await user.click(await screen.findByRole("link", { name: "ACME Incorporated" }));

    expect(
      await screen.findByRole("heading", { name: "ACME Incorporated" }),
    ).toBeInTheDocument();
    expect(api.getInvoice).toHaveBeenCalledWith("inv-settled");
  });

  test("uploading an invoice opens the new invoice's URL", async () => {
    vi.mocked(api.uploadInvoice).mockResolvedValue({ id: "inv-running", status: "pending" });
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(runningActivity);
    const user = userEvent.setup();

    renderAt("/");
    const file = new File(["%PDF-1.4"], "invoice.pdf", { type: "application/pdf" });
    await user.upload(await screen.findByLabelText(/invoice pdf/i), file);
    await user.click(screen.getByRole("button", { name: /review invoice/i }));

    // The review a person watches is the review they can link someone else to.
    expect(await screen.findByText(/searching policy/i)).toBeInTheDocument();
    expect(api.getInvoice).toHaveBeenCalledWith("inv-running");
  });

  test("the Invoices tab is active on the queue and not on history", async () => {
    const { unmount } = renderAt("/");
    expect(await screen.findByRole("link", { name: "Invoices" })).toHaveClass("active");
    unmount();

    renderAt("/history");
    expect(await screen.findByRole("link", { name: "Invoices" })).not.toHaveClass("active");
  });
});

describe("the pending-vendor badge", () => {
  test("links to /vendors when vendors are waiting", async () => {
    vi.mocked(api.listPendingVendors).mockResolvedValue([pendingVendor]);
    const user = userEvent.setup();

    renderAt("/");
    await user.click(
      await screen.findByRole("link", { name: /1 vendor awaiting approval/i }),
    );

    expect(
      await screen.findByRole("heading", { name: /vendor approvals/i }),
    ).toBeInTheDocument();
  });

  test("is hidden once you are already on the vendors screen", async () => {
    vi.mocked(api.listPendingVendors).mockResolvedValue([pendingVendor]);

    renderAt("/vendors");
    await screen.findByRole("heading", { name: /vendor approvals/i });

    expect(
      screen.queryByRole("link", { name: /awaiting approval/i }),
    ).not.toBeInTheDocument();
  });
});

describe("the run settling is what swaps the view", () => {
  test("replaces the ticker with the decision, without a navigation", async () => {
    // First read is mid-run; the reload after the ticker settles finds the
    // finished transcript.
    vi.mocked(api.getInvoice)
      .mockResolvedValueOnce(runningDetail)
      .mockResolvedValue(settledDetail);
    vi.mocked(api.getActivity).mockResolvedValue(settledActivity);

    renderAt("/invoices/inv-running");

    expect(await screen.findByText(/tool call timeline/i)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /reviewing invoice/i })).not.toBeInTheDocument();
    // Proves the settle actually triggered a re-read rather than the fixture
    // simply being settled to begin with.
    expect(api.getInvoice).toHaveBeenCalledTimes(2);
  });

  test("keeps the last good view when a refresh fails", async () => {
    vi.mocked(api.getInvoice)
      .mockResolvedValueOnce(runningDetail)
      .mockRejectedValue(new Error("Request failed: 503 Service Unavailable"));
    vi.mocked(api.getActivity).mockResolvedValue(settledActivity);

    renderAt("/invoices/inv-running");
    await screen.findByRole("heading", { name: /reviewing invoice/i });

    // The failed reload must not replace the page with a bare error string.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByRole("heading", { name: /reviewing invoice/i })).toBeInTheDocument();
    expect(screen.queryByText(/503 service unavailable/i)).not.toBeInTheDocument();
  });

  test("still reports a failure on the very first load", async () => {
    vi.mocked(api.getInvoice).mockRejectedValue(new Error("Request failed: 404 Not Found"));

    renderAt("/invoices/inv-missing");

    expect(await screen.findByText(/404 not found/i)).toBeInTheDocument();
  });
});

describe("every screen has a way out", () => {
  test("a review in flight can be left without waiting for it", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(runningActivity);
    const user = userEvent.setup();

    renderAt("/invoices/inv-running");
    await user.click(await screen.findByRole("link", { name: /back to queue/i }));

    expect(await screen.findByRole("heading", { name: /upload an invoice/i })).toBeInTheDocument();
  });

  test("a stuck review can be inspected instead of only watched", async () => {
    // A backend restart leaves agent_runs.status at "running" forever, so the
    // ticker would otherwise be the only thing this invoice ever shows.
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(runningActivity);
    const user = userEvent.setup();

    renderAt("/invoices/inv-running");
    await user.click(await screen.findByRole("button", { name: /show details anyway/i }));

    expect(await screen.findByText(/tool call timeline/i)).toBeInTheDocument();
    expect(screen.getByText("PO-77")).toBeInTheDocument();
  });

  test("a settled invoice links back to the queue", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);
    const user = userEvent.setup();

    renderAt("/invoices/inv-settled");
    await user.click(await screen.findByRole("link", { name: /back to queue/i }));

    expect(await screen.findByRole("heading", { name: /upload an invoice/i })).toBeInTheDocument();
  });

  test("the vendors screen links back to the queue", async () => {
    vi.mocked(api.listPendingVendors).mockResolvedValue([pendingVendor]);
    const user = userEvent.setup();

    renderAt("/vendors");
    await user.click(await screen.findByRole("link", { name: /back to queue/i }));

    expect(await screen.findByRole("heading", { name: /upload an invoice/i })).toBeInTheDocument();
  });

  test("an unknown URL offers a way home rather than a framework error", async () => {
    renderAt("/invoces/inv-settled");

    expect(await screen.findByRole("heading", { name: /page not found/i })).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: /back to queue/i }));
    expect(await screen.findByRole("heading", { name: /upload an invoice/i })).toBeInTheDocument();
  });
});

describe("deciding an invoice returns to the queue", () => {
  test("approving sends the reviewer back to the queue", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);
    vi.mocked(api.approveInvoice).mockResolvedValue(settledSummary);
    const user = userEvent.setup();

    renderAt("/invoices/inv-settled");
    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("heading", { name: /upload an invoice/i })).toBeInTheDocument();
    expect(api.approveInvoice).toHaveBeenCalledWith("inv-settled", "");
  });
});

describe("the nav reflects where you are", () => {
  test("Invoices stays lit on an invoice's own page", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);

    renderAt("/invoices/inv-settled");
    await screen.findByRole("heading", { name: "ACME Incorporated" });

    expect(screen.getByRole("link", { name: "Invoices" })).toHaveClass("active");
    expect(screen.getByRole("link", { name: "History" })).not.toHaveClass("active");
  });

  test("the badge stays hidden on the vendors screen even with a trailing slash", async () => {
    vi.mocked(api.listPendingVendors).mockResolvedValue([pendingVendor]);

    renderAt("/vendors/");
    await screen.findByRole("heading", { name: /vendor approvals/i });

    expect(screen.queryByRole("link", { name: /awaiting approval/i })).not.toBeInTheDocument();
  });
});

describe("history", () => {
  test("lists decisions a person has already made", async () => {
    vi.mocked(api.listAuditLog).mockResolvedValue([auditEntry]);

    renderAt("/history");

    expect(await screen.findByText("ACME Incorporated")).toBeInTheDocument();
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText(/checked against the PO by hand/i)).toBeInTheDocument();
  });
});
