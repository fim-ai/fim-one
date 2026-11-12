import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import admin from "../../../../messages/en/admin.json"
import errors from "../../../../messages/en/errors.json"
import { AdminAnalytics } from "@/components/admin/admin-analytics"

// The bodies are what /api/admin/analytics/* return (field names as the API sends them).
const api = vi.hoisted(() => ({
  getUsageAnalytics: vi.fn(),
  getUsageTrends: vi.fn(),
  getAnalyticsByAgent: vi.fn(),
  getAnalyticsByConnector: vi.fn(),
  getAnalyticsByWorkflow: vi.fn(),
  getCostProjection: vi.fn(),
}))
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  adminApi: api,
}))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

function renderAnalytics() {
  return render(
    <NextIntlClientProvider locale="en" messages={{ admin, errors }}>
      <AdminAnalytics />
    </NextIntlClientProvider>,
  )
}

beforeEach(() => {
  api.getUsageAnalytics.mockResolvedValue([])
  api.getUsageTrends.mockResolvedValue([])
  api.getAnalyticsByAgent.mockResolvedValue([
    {
      agent_id: "a1",
      agent_name: "Helper",
      owner_id: "u1",
      owner_username: "ada",
      total_conversations: 4,
      total_tokens: 12_000,
      avg_tokens_per_conversation: 3_000,
    },
  ])
  api.getAnalyticsByWorkflow.mockResolvedValue([
    {
      workflow_id: "w1",
      workflow_name: "Nightly",
      owner_id: "u1",
      owner_username: "ada",
      total_runs: 7,
      success_count: 7,
      success_rate: 100,
      avg_duration_ms: 1200,
    },
  ])
  api.getCostProjection.mockResolvedValue({
    trailing_7d_tokens: 70_000,
    daily_avg_tokens: 10_000,
    projected_30d_tokens: 300_000,
    trailing_7d_conversations: 12,
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("AdminAnalytics reads the fields the API returns", () => {
  it("By Agent shows the owner, conversations and average tokens per conversation", async () => {
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "By Agent" }))
    const row = (await screen.findByText("Helper")).closest("tr")!
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent)
    expect(cells).toEqual(["Helper", "ada", "4", "12.0K", "3.0K"])
  })

  it("By Workflow shows the owner", async () => {
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "By Workflow" }))
    const row = (await screen.findByText("Nightly")).closest("tr")!
    expect(within(row).getAllByRole("cell")[1].textContent).toBe("ada")
  })

  it("Cost Projection shows the projected, daily and trailing totals", async () => {
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "Cost Projection" }))
    const figure = async (label: string) =>
      (await screen.findByText(label)).nextElementSibling?.textContent
    expect(await figure("Projected Tokens (30d)")).toBe("300.0K")
    expect(await figure("Daily Average")).toBe("10.0K")
    expect(await figure("Trailing Total")).toBe("70.0K")
  })

  it("Cost Projection without usage says there is not enough data", async () => {
    api.getCostProjection.mockResolvedValue({
      trailing_7d_tokens: 0,
      daily_avg_tokens: 0,
      projected_30d_tokens: 0,
      trailing_7d_conversations: 0,
    })
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "Cost Projection" }))
    expect(await screen.findByText("Insufficient data for cost projection.")).toBeTruthy()
  })
})

describe("AdminAnalytics shows a dash for an average the API leaves out", () => {
  it("By Agent: a null average tokens per conversation is a dash, not 0", async () => {
    api.getAnalyticsByAgent.mockResolvedValue([
      {
        agent_id: "a2",
        agent_name: "Emptied",
        owner_id: "u1",
        owner_username: "ada",
        total_conversations: 0,
        total_tokens: 0,
        avg_tokens_per_conversation: null,
      },
    ])
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "By Agent" }))
    const row = (await screen.findByText("Emptied")).closest("tr")!
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent)
    expect(cells[4]).toBe("\u2014")
  })

  it("By Connector: null success rate and response time are dashes", async () => {
    api.getAnalyticsByConnector.mockResolvedValue([
      {
        kind: "connector",
        connector_id: "c1",
        connector_name: "CRM",
        total_calls: 0,
        success_count: 0,
        error_count: 0,
        success_rate: null,
        avg_response_time_ms: null,
      },
    ])
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "By Connector" }))
    const row = (await screen.findByText("CRM")).closest("tr")!
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent)
    expect(cells[2]).toBe("\u2014")
    expect(cells[3]).toBe("\u2014")
  })

  it("By Workflow: null success rate and duration are dashes", async () => {
    api.getAnalyticsByWorkflow.mockResolvedValue([
      {
        workflow_id: "w2",
        workflow_name: "Idle",
        owner_id: "u1",
        owner_username: "ada",
        total_runs: 0,
        success_count: 0,
        success_rate: null,
        avg_duration_ms: null,
      },
    ])
    renderAnalytics()
    fireEvent.click(screen.getByRole("button", { name: "By Workflow" }))
    const row = (await screen.findByText("Idle")).closest("tr")!
    const cells = within(row).getAllByRole("cell").map((cell) => cell.textContent)
    expect(cells[3]).toBe("\u2014")
    expect(cells[4]).toBe("\u2014")
  })
})
