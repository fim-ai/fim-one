import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { WorkflowStatsPanel } from "@/components/workflows/workflow-stats-panel"

const api = vi.hoisted(() => ({ getStats: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("@/hooks/use-date-formatter", () => ({
  useDateFormatter: () => ({ formatRelativeTime: () => "a moment ago" }),
}))

afterEach(cleanup)

describe("WorkflowStatsPanel", () => {
  // GET /api/workflows/{id}/stats sends the rate as a percentage (0-100),
  // as every other success rate; the panel multiplied it by 100 again.
  it("shows the success rate the server sends as a percentage", async () => {
    api.getStats.mockResolvedValue({
      total_runs: 4,
      completed: 3,
      failed: 1,
      cancelled: 0,
      success_rate: 75,
      avg_duration_ms: 1200,
      last_run_at: null,
    })
    render(
      <NextIntlClientProvider locale="en" messages={{ workflows, common, errors }} timeZone="UTC">
        <WorkflowStatsPanel workflowId="wf1" open onOpenChange={() => {}} />
      </NextIntlClientProvider>,
    )
    expect(await screen.findByText("75.0%")).toBeTruthy()
    expect(screen.queryByText("7500.0%")).toBeNull()
  })
})
