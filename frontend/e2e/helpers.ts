import { expect, type APIRequestContext, type Locator, type Page } from "@playwright/test"

const API = process.env.E2E_API_URL ?? "http://127.0.0.1:8765"
export const STUB = process.env.E2E_STUB_URL ?? "http://127.0.0.1:8766"
const MCP_STUB_PATH = process.env.E2E_MCP_STUB_PATH ?? ""

export interface Session {
  accessToken: string
  refreshToken: string
  user: Record<string, unknown>
}

export interface Seed {
  session: Session
  connectorId: string
  connectorName: string
  actionId: string
  actionName: string
  dbConnectorId: string
  dbConnectorName: string
  mcpServerId: string
  mcpServerName: string
  kbId: string
  kbName: string
}

export const KB_FACT = "the sky over the stub is green"

async function api<T>(
  request: APIRequestContext,
  method: "GET" | "POST" | "PATCH" | "PUT",
  path: string,
  token: string | null,
  data?: unknown,
): Promise<T> {
  const res = await request.fetch(`${API}${path}`, {
    method,
    data,
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
  expect(res.ok(), `${method} ${path} -> ${res.status()} ${await res.text()}`).toBe(true)
  return (await res.json()) as T
}

/**
 * Prepare the fresh backend: first admin, the Workflows module switched on,
 * and one connector, MCP server and knowledge base that point at the stubs.
 */
export async function seed(request: APIRequestContext): Promise<Seed> {
  const auth = await api<{ access_token: string; refresh_token: string; user: Record<string, unknown> }>(
    request,
    "POST",
    "/api/auth/setup",
    null,
    { email: "smoke@example.com", password: "smoke-password-1" },
  )
  const token = auth.access_token
  await api(request, "PATCH", "/api/admin/settings", token, { modules: { workflows: true } })

  const connectorName = "Stub Echo"
  const connector = await api<{ data: { id: string } }>(request, "POST", "/api/connectors", token, {
    name: connectorName,
    base_url: STUB,
    auth_type: "none",
  })
  const actionName = "echo"
  const action = await api<{ data: { id: string } }>(
    request,
    "POST",
    `/api/connectors/${connector.data.id}/actions`,
    token,
    {
      name: actionName,
      method: "GET",
      path: "/connector/echo",
      parameters_schema: { type: "object", properties: { q: { type: "string" } } },
    },
  )

  // A database connector with nowhere to connect to: listing its tables
  // answers from the schema it has stored (none), without a connection.
  const dbConnectorName = "Stub Warehouse"
  const dbConnector = await api<{ data: { id: string } }>(
    request,
    "POST",
    "/api/connectors",
    token,
    {
      name: dbConnectorName,
      type: "database",
      db_config: {
        driver: "postgresql",
        host: "127.0.0.1",
        port: 1,
        database: "stub",
        username: "stub",
        password: "stub",
      },
    },
  )

  const mcpServerName = "Stub MCP"
  const mcp = await api<{ data: { id: string } }>(request, "POST", "/api/mcp-servers", token, {
    name: mcpServerName,
    transport: "stdio",
    command: "node",
    args: [MCP_STUB_PATH],
  })

  const kbName = "Stub KB"
  const kb = await api<{ data: { id: string } }>(request, "POST", "/api/knowledge-bases", token, {
    name: kbName,
    retrieval_mode: "dense",
  })
  await api(request, "POST", `/api/knowledge-bases/${kb.data.id}/documents/create`, token, {
    filename: "smoke.md",
    content: `# Smoke\n\nThe smoke test fact: ${KB_FACT}.`,
  })
  await expect
    .poll(
      async () => {
        const docs = await api<{ items: Array<{ status: string }> }>(
          request,
          "GET",
          `/api/knowledge-bases/${kb.data.id}/documents`,
          token,
        )
        return docs.items.map((d) => d.status).join(",")
      },
      { timeout: 60_000, message: "knowledge base document is ingested" },
    )
    .toBe("ready")

  return {
    session: { accessToken: token, refreshToken: auth.refresh_token, user: auth.user },
    connectorId: connector.data.id,
    connectorName,
    actionId: action.data.id,
    actionName,
    dbConnectorId: dbConnector.data.id,
    dbConnectorName,
    mcpServerId: mcp.data.id,
    mcpServerName,
    kbId: kb.data.id,
    kbName,
  }
}

/** Create a workflow with nothing on the canvas and return its id. */
export async function createEmptyWorkflow(
  request: APIRequestContext,
  session: Session,
  name: string,
): Promise<string> {
  const created = await api<{ data: { id: string } }>(
    request,
    "POST",
    "/api/workflows",
    session.accessToken,
    {
      name,
      blueprint: { schema_version: 2, nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } },
    },
  )
  return created.data.id
}

/** Create a saved Start -> LLM -> End workflow and return its id. */
export async function createLinearWorkflow(
  request: APIRequestContext,
  session: Session,
  name: string,
  prompt = "Say {{start_1.topic}}",
  variables?: Array<Record<string, unknown>>,
): Promise<string> {
  const created = await api<{ data: { id: string } }>(
    request,
    "POST",
    "/api/workflows",
    session.accessToken,
    {
      name,
      blueprint: {
        schema_version: 2,
        nodes: [
          {
            id: "start_1",
            type: "start",
            position: { x: 100, y: 200 },
            data: { variables: [{ name: "topic", type: "string", required: false }] },
          },
          {
            id: "llm_1",
            type: "llm",
            position: { x: 420, y: 200 },
            data: { prompt_template: prompt, output_variable: "llm_result", temperature: 0.7 },
          },
          {
            id: "end_1",
            type: "end",
            position: { x: 740, y: 200 },
            data: { output_mapping: { out: "{{llm_1.llm_result}}" } },
          },
        ],
        edges: [
          { id: "e1", source: "start_1", target: "llm_1", sourceHandle: "source", targetHandle: "target" },
          { id: "e2", source: "llm_1", target: "end_1", sourceHandle: "source", targetHandle: "target" },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
        ...(variables ? { variables } : {}),
      },
    },
  )
  return created.data.id
}

/** Create a saved Start -> Connector -> End workflow whose connector is not chosen yet. */
export async function createConnectorWorkflow(
  request: APIRequestContext,
  session: Session,
  name: string,
): Promise<string> {
  const created = await api<{ data: { id: string } }>(
    request,
    "POST",
    "/api/workflows",
    session.accessToken,
    {
      name,
      blueprint: {
        schema_version: 2,
        nodes: [
          { id: "start_1", type: "start", position: { x: 100, y: 200 }, data: { variables: [] } },
          {
            id: "connector_1",
            type: "connector",
            position: { x: 420, y: 200 },
            data: { connector_id: "", action_id: "", parameters: {}, output_variable: "result" },
          },
          {
            id: "end_1",
            type: "end",
            position: { x: 740, y: 200 },
            data: { output_mapping: { out: "{{connector_1.result}}" } },
          },
        ],
        edges: [
          { id: "e1", source: "start_1", target: "connector_1", sourceHandle: "source", targetHandle: "target" },
          { id: "e2", source: "connector_1", target: "end_1", sourceHandle: "source", targetHandle: "target" },
        ],
        viewport: { x: 0, y: 0, zoom: 1 },
      },
    },
  )
  return created.data.id
}

export interface StoredBlueprint {
  variables?: Array<Record<string, unknown>> | null
  nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>
  edges: Array<{ source: string; target: string; sourceHandle?: string | null }>
}

export async function fetchBlueprint(
  request: APIRequestContext,
  session: Session,
  workflowId: string,
): Promise<StoredBlueprint> {
  const wf = await api<{ data: { blueprint: StoredBlueprint } }>(
    request,
    "GET",
    `/api/workflows/${workflowId}`,
    session.accessToken,
  )
  return wf.data.blueprint
}

/** Calls the stub model has served so far. */
export async function stubRequests(
  request: APIRequestContext,
): Promise<Array<{ path: string; lastUser?: string }>> {
  const res = await request.get(`${STUB}/__requests`)
  return ((await res.json()) as { requests: Array<{ path: string; lastUser?: string }> }).requests
}

/** Sign the browser in by placing the session where the app keeps it. */
export async function signIn(page: Page, session: Session): Promise<void> {
  await page.addInitScript((s: Session) => {
    window.localStorage.setItem("fim_access_token", s.accessToken)
    window.localStorage.setItem("fim_refresh_token", s.refreshToken)
    window.localStorage.setItem("fim_user", JSON.stringify(s.user))
  }, session)
}

// ---------------------------------------------------------------------------
// Canvas
// ---------------------------------------------------------------------------

export const canvas = (page: Page): Locator => page.locator(".react-flow")
export const canvasNodes = (page: Page): Locator => page.locator(".react-flow__node")
export const canvasEdges = (page: Page): Locator => page.locator(".react-flow__edge")
export const nodeOfType = (page: Page, type: string): Locator =>
  page.locator(`.react-flow__node-${type}`)
export const paletteItem = (page: Page, type: string): Locator =>
  page.getByTestId(`palette-item-${type}`)
export const configPanel = (page: Page): Locator => page.getByTestId("node-config-panel")

/** Drag a node type from the palette onto the canvas at a canvas-relative point. */
export async function addNode(
  page: Page,
  type: string,
  at: { x: number; y: number },
): Promise<void> {
  const before = await nodeOfType(page, type).count()
  await paletteItem(page, type).dragTo(page.locator(".react-flow__pane"), { targetPosition: at })
  await expect(nodeOfType(page, type)).toHaveCount(before + 1)
}

/** Draw an edge by dragging from a source handle to the target node's input handle. */
export async function connect(
  page: Page,
  from: { type: string; handle?: string },
  toType: string,
): Promise<void> {
  const before = await canvasEdges(page).count()
  const source = from.handle
    ? nodeOfType(page, from.type).locator(`.react-flow__handle[data-handleid="${from.handle}"]`)
    : nodeOfType(page, from.type).locator(".react-flow__handle.source")
  const target = nodeOfType(page, toType).locator(".react-flow__handle.target")
  const a = await source.boundingBox()
  const b = await target.boundingBox()
  if (!a || !b) throw new Error(`handles of ${from.type} -> ${toType} are not on screen`)
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2, { steps: 3 })
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 12 })
  await page.mouse.up()
  await expect(canvasEdges(page)).toHaveCount(before + 1)
}

/** The canvas element; it carries the editor's "settled" state as data attributes. */
export const canvasState = (page: Page): Locator => page.getByTestId("workflow-canvas")

/**
 * Wait until the canvas has handed its current state to the page. It does so
 * a moment after the last change; until then the page cannot know whether
 * the workflow is unsaved.
 */
export async function canvasReported(page: Page): Promise<void> {
  const state = canvasState(page)
  await expect(state).not.toHaveAttribute("data-sync-reports", "0")
  await expect(state).toHaveAttribute("data-sync-pending", "false")
}

/** Wait until undo history has recorded the canvas as it is (it does so a moment after a change). */
export async function historyRecorded(page: Page): Promise<void> {
  const state = canvasState(page)
  await expect(state).not.toHaveAttribute("data-history-revision", "0")
  await expect(state).toHaveAttribute("data-history-pending", "false")
}

/** Do something to the canvas and wait until undo history holds the result as a step of its own. */
export async function asHistoryStep(page: Page, action: () => Promise<void>): Promise<void> {
  const state = canvasState(page)
  const revision = async () => Number(await state.getAttribute("data-history-revision"))
  const before = await revision()
  await action()
  await expect
    .poll(
      async () =>
        (await revision()) > before && (await state.getAttribute("data-history-pending")) === "false",
      { message: "undo history recorded the change" },
    )
    .toBe(true)
}

/** Select a node and wait for its settings panel. */
export async function openConfig(page: Page, type: string): Promise<Locator> {
  await nodeOfType(page, type).click()
  const panel = configPanel(page)
  await expect(panel).toBeVisible()
  return panel
}

/** The id React Flow gave the single node of a type. */
export async function nodeId(page: Page, type: string): Promise<string> {
  const id = await nodeOfType(page, type).getAttribute("data-id")
  if (!id) throw new Error(`no ${type} node on the canvas`)
  return id
}
