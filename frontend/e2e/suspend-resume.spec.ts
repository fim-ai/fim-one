import { expect, test, type APIRequestContext, type Page } from "@playwright/test"

import { signIn, type Session } from "./helpers"

/**
 * Runs that wait: the Start step's input check, and a run that stops at a
 * Human Review step until someone decides.
 *
 * Human Review steps are behind a switch that is off as shipped, and the
 * backend the web app is built against keeps it off (the editor smoke
 * asserts that). This spec uses the second backend of playwright.config.ts,
 * which has the switch on: every /api call of the page is routed to it.
 */

const API = process.env.E2E_APPROVALS_API_URL ?? "http://127.0.0.1:8767"

let session: Session

async function api<T>(
  request: APIRequestContext,
  method: "GET" | "POST" | "PATCH",
  path: string,
  data?: unknown,
  token: string | null = session?.accessToken ?? null,
): Promise<T> {
  const res = await request.fetch(`${API}${path}`, {
    method,
    data,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
  expect(res.ok(), `${method} ${path} -> ${res.status()} ${await res.text()}`).toBe(true)
  return (await res.json()) as T
}

/** Send the page's API calls to the backend that has Human Review on. */
async function useApprovalsBackend(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname.startsWith("/api/"),
    async (route) => {
      const url = new URL(route.request().url())
      try {
        const response = await route.fetch({ url: `${API}${url.pathname}${url.search}` })
        await route.fulfill({ response })
      } catch {
        // The page went away while the request was on its way.
        await route.abort().catch(() => {})
      }
    },
  )
}

interface RunRecord {
  id: string
  status: string
  inputs: Record<string, unknown> | null
  outputs: Record<string, unknown> | null
  waiting_for?: Array<{ confirmation_id: string }> | null
}

async function latestRun(request: APIRequestContext, workflowId: string): Promise<RunRecord | null> {
  const page = await api<{ items: RunRecord[] }>(
    request,
    "GET",
    `/api/workflows/${workflowId}/runs?page=1&size=1`,
  )
  return page.items[0] ?? null
}

async function runCount(request: APIRequestContext, workflowId: string): Promise<number> {
  const page = await api<{ total: number }>(
    request,
    "GET",
    `/api/workflows/${workflowId}/runs?page=1&size=1`,
  )
  return page.total
}

async function createWorkflow(
  request: APIRequestContext,
  name: string,
  nodes: Array<Record<string, unknown>>,
): Promise<string> {
  const edges = nodes.slice(1).map((node, i) => ({
    id: `e${i + 1}`,
    source: nodes[i].id,
    target: node.id,
    sourceHandle: "source",
    targetHandle: "target",
  }))
  const created = await api<{ data: { id: string } }>(request, "POST", "/api/workflows", {
    name,
    blueprint: { schema_version: 2, nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } },
  })
  return created.data.id
}

const approvalWorkflow = (request: APIRequestContext, name: string) =>
  createWorkflow(request, name, [
    { id: "start_1", type: "start", position: { x: 100, y: 200 }, data: { variables: [] } },
    {
      id: "human_1",
      type: "humanIntervention",
      position: { x: 420, y: 200 },
      data: {
        label: "Release check",
        title: "Ship the release?",
        prompt_message: "Ship the release?",
        assignee: "",
        timeout_hours: 24,
        output_variable: "approval_result",
      },
    },
    {
      id: "end_1",
      type: "end",
      position: { x: 740, y: 200 },
      data: { output_mapping: { done: "yes" } },
    },
  ])

/** Open the editor, start a run from the panel and wait until it waits. */
async function runUntilWaiting(page: Page, workflowId: string) {
  await page.goto(`/workflows/${workflowId}`)
  await expect(page.locator(".react-flow__pane")).toBeVisible()
  await page.getByRole("button", { name: "Run", exact: true }).click()
  const runPanel = page.getByTestId("run-panel")
  await runPanel.getByRole("button", { name: "Start Run" }).click()
  await expect(runPanel.getByTestId("run-status")).toHaveAttribute("data-status", "waiting", {
    timeout: 60_000,
  })
  return runPanel
}

test.describe.configure({ mode: "serial" })

test.beforeAll(async ({ request }) => {
  const auth = await api<{ access_token: string; refresh_token: string; user: Record<string, unknown> }>(
    request,
    "POST",
    "/api/auth/setup",
    { email: "approver@example.com", password: "smoke-password-1" },
    null,
  )
  session = { accessToken: auth.access_token, refreshToken: auth.refresh_token, user: auth.user }
  await api(request, "PATCH", "/api/admin/settings", { modules: { workflows: true } })
  const version = await api<{ workflow_human_intervention?: boolean }>(request, "GET", "/api/version")
  expect(version.workflow_human_intervention, "the second backend has Human Review on").toBe(true)
})

test.beforeEach(async ({ page }) => {
  await useApprovalsBackend(page)
  await signIn(page, session)
})

test("a Start step that checks inputs refuses text for a Number and converts a numeral", async ({
  page,
  request,
}) => {
  const workflowId = await createWorkflow(request, "Typed inputs", [
    {
      id: "start_1",
      type: "start",
      position: { x: 100, y: 200 },
      data: {
        variables: [{ name: "count", type: "number", required: true }],
        validate_inputs: true,
      },
    },
    {
      id: "end_1",
      type: "end",
      position: { x: 600, y: 200 },
      data: { output_mapping: { out: "{{start_1.count}}" } },
    },
  ])
  await page.goto(`/workflows/${workflowId}`)
  await expect(page.locator(".react-flow__pane")).toBeVisible()

  await test.step("the Start form shows the type by its name and the check switched on", async () => {
    await page.locator(".react-flow__node-start").click()
    const config = page.getByTestId("node-config-panel")
    await expect(config.getByText("Number", { exact: true })).toBeVisible()
    await expect(
      config.getByRole("switch", { name: "Check inputs before a run starts" }),
    ).toHaveAttribute("aria-checked", "true")
  })

  await page.getByRole("button", { name: "Run", exact: true }).click()
  const runPanel = page.getByTestId("run-panel")
  const field = runPanel.getByLabel(/^count/)

  await test.step('"abc" is refused on the field and no run starts', async () => {
    await field.fill("abc")
    await runPanel.getByRole("button", { name: "Start Run" }).click()
    const error = runPanel.getByTestId("run-input-count").locator("p.text-destructive")
    await expect(error).toHaveText('Input "count" must be a number.')
    await expect(field).toHaveAttribute("aria-invalid", "true")
    await expect(field).toHaveValue("abc")
    await expect(runPanel.getByTestId("run-status")).toHaveCount(0)
    expect(await runCount(request, workflowId)).toBe(0)
  })

  await test.step("an empty required input is refused as well", async () => {
    await field.fill("")
    await runPanel.getByRole("button", { name: "Start Run" }).click()
    await expect(runPanel.getByTestId("run-input-count").locator("p.text-destructive")).toHaveText(
      'Input "count" is required.',
    )
    expect(await runCount(request, workflowId)).toBe(0)
  })

  await test.step('"12" runs and the steps get the number 12', async () => {
    await field.fill("12")
    await expect(runPanel.getByTestId("run-input-count").locator("p.text-destructive")).toHaveCount(0)
    await runPanel.getByRole("button", { name: "Start Run" }).click()
    await expect(runPanel.getByTestId("run-status")).toHaveAttribute("data-status", "completed", {
      timeout: 60_000,
    })
    const run = await latestRun(request, workflowId)
    expect(run?.status).toBe("completed")
    // A number, not the text that was typed.
    expect(run?.inputs?.count).toBe(12)
    // The End step's mapping is a text template; it renders the number.
    expect(String(run?.outputs?.out)).toBe("12")
  })
})

test("a run that stops at a Human Review step waits, is approved from the inbox and completes", async ({
  page,
  request,
}) => {
  const workflowId = await approvalWorkflow(request, "Release approval")
  const runPanel = await runUntilWaiting(page, workflowId)

  await test.step("the run panel shows the run as waiting and offers the decision", async () => {
    await expect(runPanel.getByTestId("run-status")).toContainText("Waiting for a decision")
    const waiting = runPanel.getByTestId("run-waiting")
    await expect(waiting.getByTestId("run-waiting-item")).toHaveCount(1)
    await expect(waiting).toContainText("Release check")
    await expect(waiting.getByRole("button", { name: "Approve" })).toBeVisible()
    await expect(waiting.getByRole("button", { name: "Reject" })).toBeVisible()
    await expect(runPanel.getByRole("button", { name: "Cancel Run" })).toBeVisible()
    expect((await latestRun(request, workflowId))?.status).toBe("waiting")
  })

  await test.step("reopening the editor shows the waiting run again", async () => {
    await page.reload()
    const reopened = page.getByTestId("run-panel")
    await expect(reopened.getByTestId("run-status")).toHaveAttribute("data-status", "waiting")
    await expect(reopened.getByTestId("run-waiting").getByRole("button", { name: "Approve" })).toBeVisible()
    // Leaving and coming back cancelled nothing.
    expect((await latestRun(request, workflowId))?.status).toBe("waiting")
  })

  await test.step("the workflows page links to the inbox with a count of 1", async () => {
    await page.goto("/workflows")
    const link = page.getByTestId("approvals-link")
    await expect(link.getByTestId("approvals-count")).toHaveText("1")
    await link.click()
    await page.waitForURL(/\/workflows\/approvals$/)
  })

  await test.step("approving with a note empties the inbox", async () => {
    const card = page.getByTestId("approval-card")
    await expect(card).toHaveCount(1)
    await expect(card.getByRole("link", { name: "Release approval" })).toHaveAttribute(
      "href",
      `/workflows/${workflowId}`,
    )
    await expect(card).toContainText("Step: Release check")
    await expect(card).toContainText("Ship the release?")
    await card.getByLabel("Note").fill("Checked the changelog")
    await card.getByRole("button", { name: "Approve" }).click()
    await expect(page.getByText("Approved. The run continues.")).toBeVisible()
    await expect(page.getByTestId("approval-card")).toHaveCount(0)
    await expect(page.getByText("Nothing waits for your decision")).toBeVisible()
  })

  await test.step("the run goes on and completes", async () => {
    await expect
      .poll(async () => (await latestRun(request, workflowId))?.status, {
        timeout: 60_000,
        message: "the approved run completes",
      })
      .toBe("completed")
  })

  await test.step("the count badge is gone", async () => {
    await page.goto("/workflows")
    await expect(page.getByTestId("approvals-link")).toBeVisible()
    await expect(page.getByTestId("approvals-count")).toHaveCount(0)
  })

  await test.step("Run History shows the run as completed", async () => {
    await page.goto(`/workflows/${workflowId}`)
    await expect(page.locator(".react-flow__pane")).toBeVisible()
    // The run has ended: the panel does not open by itself.
    await expect(page.getByTestId("run-panel")).toHaveCount(0)
    await page.getByTestId("workflow-more-menu").click()
    await page.getByRole("menuitem", { name: "Run History", exact: true }).click()
    const sheet = page.getByRole("dialog")
    await expect(sheet.getByText("Completed")).toHaveCount(1)
    await expect(sheet.getByText("Waiting", { exact: true })).toHaveCount(0)
  })
})

test("deciding in the run panel moves the run on, and the panel follows it to the end", async ({
  page,
  request,
}) => {
  const workflowId = await approvalWorkflow(request, "Approve in place")
  const runPanel = await runUntilWaiting(page, workflowId)
  await runPanel.getByTestId("run-waiting").getByRole("button", { name: "Approve" }).click()
  await expect(runPanel.getByTestId("run-status")).toHaveAttribute("data-status", "completed", {
    timeout: 60_000,
  })
  await expect(runPanel.getByTestId("run-waiting")).toHaveCount(0)
  expect((await latestRun(request, workflowId))?.status).toBe("completed")
})

test("Cancel in the run panel cancels a waiting run", async ({ page, request }) => {
  const workflowId = await approvalWorkflow(request, "Cancel while waiting")
  const runPanel = await runUntilWaiting(page, workflowId)
  await runPanel.getByRole("button", { name: "Cancel Run" }).click()
  await expect(runPanel.getByTestId("run-status")).toHaveAttribute("data-status", "cancelled")
  await expect
    .poll(async () => (await latestRun(request, workflowId))?.status, { message: "the run is cancelled" })
    .toBe("cancelled")
})

test("a request decided elsewhere shows how it ended instead of an error", async ({ page, request }) => {
  const workflowId = await approvalWorkflow(request, "Decided elsewhere")
  await runUntilWaiting(page, workflowId)
  const run = await latestRun(request, workflowId)
  const confirmationId = run?.waiting_for?.[0]?.confirmation_id
  expect(confirmationId, "the waiting run names its request").toBeTruthy()

  await page.goto("/workflows/approvals")
  const card = page.getByTestId("approval-card")
  await expect(card).toHaveCount(1)

  // Another surface rejects it first.
  await api(request, "POST", `/api/confirmations/${confirmationId}/respond`, { decision: "reject" })
  // The page may notice by itself (it reads the inbox every few seconds);
  // if it has not yet, the click is what tells it.
  await card
    .getByRole("button", { name: "Approve" })
    .click({ timeout: 3_000 })
    .catch(() => {})
  await expect(card).toContainText("Already rejected.")
  await expect(card.getByRole("button", { name: "Approve" })).toHaveCount(0)
  await expect(page.getByText("already been decided")).toHaveCount(0)
  await card.getByRole("button", { name: "Dismiss" }).click()
  await expect(page.getByTestId("approval-card")).toHaveCount(0)
})
