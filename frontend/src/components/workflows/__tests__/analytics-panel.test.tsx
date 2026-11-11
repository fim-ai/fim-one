import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { AnalyticsPanel } from "@/components/workflows/analytics-panel"

const api = vi.hoisted(() => ({ getAnalytics: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))

beforeAll(() => {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub)
})
afterEach(cleanup)

describe("AnalyticsPanel", () => {
  // GET /api/workflows/{id}/analytics when every run in the range was
  // cancelled or is still running: no run finished, so there is no success
  // rate, and none of them recorded its steps. The panel called toFixed on
  // the nulls and the page crashed.
  it("shows runs none of which finished, without a success rate", async () => {
    api.getAnalytics.mockResolvedValue({
      total_runs: 2,
      status_distribution: { cancelled: 1, running: 1 },
      success_rate: null,
      avg_duration_ms: null,
      p50_duration_ms: null,
      p95_duration_ms: null,
      p99_duration_ms: null,
      runs_per_day: [],
      most_failed_nodes: [],
      avg_nodes_per_run: null,
    })
    render(
      <NextIntlClientProvider locale="en" messages={{ workflows, common, errors }} timeZone="UTC">
        <AnalyticsPanel workflowId="wf1" open onOpenChange={() => {}} nodeTypeMap={{}} />
      </NextIntlClientProvider>,
    )
    const label = await screen.findByText("Success Rate")
    expect(label.parentElement?.parentElement?.textContent).toContain("—")
    expect(screen.queryByText(/NaN|null/)).toBeNull()
  })
})
