import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, test, vi } from "vitest";

import * as api from "../api";
import { renderApp } from "./renderApp";
import {
  auditEntry,
  pendingVendor,
  runningActivity,
  runningDetail,
  runningSummary,
  settledActivity,
  settledDetail,
  settledSummary,
} from "./fixtures";

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

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listInvoices).mockResolvedValue([settledSummary]);
  vi.mocked(api.listAuditLog).mockResolvedValue([auditEntry]);
  vi.mocked(api.listPendingVendors).mockResolvedValue([pendingVendor]);
  vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);
});

describe("components asking the same question share one answer", () => {
  test("the badge and the vendor queue cause a single request", async () => {
    // App polls /vendors/pending for the badge count; VendorApprovals reads the
    // same endpoint for the list. Two components, one question.
    renderApp("/vendors");
    await screen.findByRole("heading", { name: /vendor approvals/i });

    expect(api.listPendingVendors).toHaveBeenCalledTimes(1);
  });
});

describe("a screen already read is not read again", () => {
  test("returning to the queue inside its freshness window costs nothing", async () => {
    // This is the test that makes the invalidation tests below mean anything:
    // it establishes that a remount alone does NOT re-read. Without it, every
    // "was it refreshed?" assertion passes for the trivial reason that nothing
    // is cached at all.
    const user = userEvent.setup();

    renderApp("/");
    await screen.findByRole("link", { name: "ACME Incorporated" });
    expect(api.listInvoices).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("link", { name: "History" }));
    await screen.findByText(/checked against the PO by hand/i);
    await user.click(screen.getByRole("link", { name: "Invoices" }));
    await screen.findByRole("link", { name: "ACME Incorporated" });

    expect(api.listInvoices).toHaveBeenCalledTimes(1);
  });
});

describe("a decision refreshes what it invalidated", () => {
  test("approving an invoice refreshes the queue behind it", async () => {
    vi.mocked(api.approveInvoice).mockResolvedValue(settledSummary);
    const user = userEvent.setup();

    renderApp("/");
    await screen.findByRole("link", { name: "ACME Incorporated" });
    expect(api.listInvoices).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("link", { name: "ACME Incorporated" }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await screen.findByRole("heading", { name: /review another invoice/i });

    // The queue is still inside its freshness window, so only an explicit
    // invalidation can have produced a second read. Without one the reviewer
    // lands back on a list that still shows the invoice they just decided.
    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledTimes(2));
  });

  test("approving an invoice refreshes the decision history", async () => {
    vi.mocked(api.approveInvoice).mockResolvedValue(settledSummary);
    const user = userEvent.setup();

    renderApp("/history");
    await screen.findByText(/checked against the PO by hand/i);
    expect(api.listAuditLog).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("link", { name: "Invoices" }));
    await user.click(await screen.findByRole("link", { name: "ACME Incorporated" }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.click(await screen.findByRole("link", { name: "History" }));

    await waitFor(() => expect(api.listAuditLog).toHaveBeenCalledTimes(2));
  });

  test("approving a vendor refreshes the vendor queue", async () => {
    vi.mocked(api.approveVendor).mockResolvedValue({
      id: pendingVendor.id,
      name: pendingVendor.name,
      approval_status: "active",
      invoices_linked: 2,
    });
    const user = userEvent.setup();

    renderApp("/vendors");
    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(api.listPendingVendors).toHaveBeenCalledTimes(2));
  });

  test("approving a vendor refreshes the invoice queue it just adopted", async () => {
    // POST /vendors/{id}/approve adopts the invoices that were waiting on that
    // payee, so the queue behind this screen is stale the moment it returns.
    vi.mocked(api.approveVendor).mockResolvedValue({
      id: pendingVendor.id,
      name: pendingVendor.name,
      approval_status: "active",
      invoices_linked: 2,
    });
    const user = userEvent.setup();

    renderApp("/");
    await screen.findByRole("link", { name: "ACME Incorporated" });
    expect(api.listInvoices).toHaveBeenCalledTimes(1);

    await user.click(await screen.findByRole("link", { name: /awaiting approval/i }));
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await user.click(await screen.findByRole("link", { name: /back to queue/i }));

    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledTimes(2));
  });
});

describe("polling stops when there is nothing left to watch", () => {
  test("the activity endpoint is not polled once the run has settled", async () => {
    vi.mocked(api.getActivity).mockResolvedValue({
      status: "complete",
      latest: null,
      call_count: 6,
      decision: "escalate",
    });

    renderApp("/invoices/inv-settled");
    await screen.findByText(/tool call timeline/i);

    // A settled run is not polled at all: the detail read already told us so.
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(api.getActivity).not.toHaveBeenCalled();
  });
});

describe("a run settling is itself an invalidation", () => {
  test("refreshes the queue the reviewer will go back to", async () => {
    vi.mocked(api.listInvoices).mockResolvedValue([runningSummary]);
    vi.mocked(api.getInvoice).mockResolvedValueOnce(runningDetail).mockResolvedValue(settledDetail);
    vi.mocked(api.getActivity).mockResolvedValue(settledActivity);
    const user = userEvent.setup();

    renderApp("/");
    await user.click(await screen.findByRole("link", { name: "Globex Corporation" }));
    await screen.findByText(/tool call timeline/i);
    await user.click(screen.getByRole("link", { name: /back to queue/i }));

    // The row still says "reviewing…" until the queue is re-read, and nothing
    // else will re-read it: the queue has no interval, and a back navigation
    // fires no focus event.
    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledTimes(2));
  });
});

describe("the ticker polls only while there is something to watch", () => {
  test("keeps polling until the run settles", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity)
      .mockResolvedValueOnce(runningActivity)
      .mockResolvedValue({ ...runningActivity, call_count: 4 });

    renderApp("/invoices/inv-running");
    await screen.findByText(/3 steps so far/i);

    // Only a second poll can produce this.
    expect(
      await screen.findByText(/4 steps so far/i, {}, { timeout: 3000 }),
    ).toBeInTheDocument();
  });

  test("stops polling once the run has settled", async () => {
    // getInvoice stays on the running fixture so the ticker remains mounted --
    // otherwise the view swaps and polling would stop for the wrong reason.
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(settledActivity);

    renderApp("/invoices/inv-running");
    await screen.findByText(/recommended escalate/i);

    const before = vi.mocked(api.getActivity).mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 2500));
    const after = vi.mocked(api.getActivity).mock.calls.length;

    // At most one more, from the invalidation the settle triggers. Still
    // polling at 1s would add two or three.
    expect(after - before).toBeLessThanOrEqual(1);
  });
});

describe("in-flight work is labelled on the control that started it", () => {
  test("only the clicked decision shows its pending label", async () => {
    vi.mocked(api.getInvoice).mockResolvedValue(settledDetail);
    let release: (value: never) => void = () => {};
    vi.mocked(api.approveInvoice).mockReturnValue(
      new Promise((resolve) => {
        release = resolve as (value: never) => void;
      }),
    );
    const user = userEvent.setup();

    renderApp("/invoices/inv-settled");
    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("button", { name: "Approving…" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    release(undefined as never);
  });
});

describe("a failed refresh never costs a screen its contents", () => {
  test("the vendor list survives a background read that fails", async () => {
    // The badge in App shares this query, so a failing poll now writes an
    // error into the query this screen renders from -- which it could not do
    // when the screen held its own state.
    vi.mocked(api.listPendingVendors)
      .mockResolvedValueOnce([pendingVendor])
      .mockRejectedValue(new Error("Request failed: 503 Service Unavailable"));
    vi.mocked(api.approveVendor).mockResolvedValue({
      id: pendingVendor.id,
      name: pendingVendor.name,
      approval_status: "active",
      invoices_linked: 0,
    });
    const user = userEvent.setup();

    renderApp("/vendors");
    await screen.findByText(pendingVendor.name);
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(api.listPendingVendors).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("heading", { name: /vendor approvals/i })).toBeInTheDocument();
  });
});

describe("uploading", () => {
  test("refreshes the queue the new invoice belongs in", async () => {
    vi.mocked(api.uploadInvoice).mockResolvedValue({ id: "inv-running", status: "pending" });
    vi.mocked(api.getInvoice).mockResolvedValue(runningDetail);
    vi.mocked(api.getActivity).mockResolvedValue(runningActivity);
    const user = userEvent.setup();

    renderApp("/");
    await screen.findByRole("link", { name: "ACME Incorporated" });
    expect(api.listInvoices).toHaveBeenCalledTimes(1);

    const file = new File(["%PDF-1.4"], "invoice.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText(/invoice pdf/i), file);
    await user.click(screen.getByRole("button", { name: /review invoice/i }));
    await screen.findByText(/searching policy/i);
    await user.click(screen.getByRole("link", { name: /back to queue/i }));

    await waitFor(() => expect(api.listInvoices).toHaveBeenCalledTimes(2));
  });
});
