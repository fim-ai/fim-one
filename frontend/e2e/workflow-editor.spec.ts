import { readFile, writeFile } from "node:fs/promises"

import { expect, test, type Page } from "@playwright/test"

import {
  KB_FACT,
  addNode,
  canvasEdges,
  canvasNodes,
  configPanel,
  connect,
  createEmptyWorkflow,
  createLinearWorkflow,
  fetchBlueprint,
  nodeId,
  nodeOfType,
  openConfig,
  paletteItem,
  seed,
  signIn,
  stubRequests,
  type Seed,
} from "./helpers"

/**
 * Workflow editor smoke: build a workflow from an empty canvas with every
 * node type the editor offers, save, reload, run it, and read the result.
 * The model, the connector target and the MCP server are stubs (e2e/stubs).
 */

// Node types the palette offers when the approval switch is off (the default).
const OFFERED = [
  "start",
  "llm",
  "conditionBranch",
  "agent",
  "knowledgeRetrieval",
  "connector",
  "mcp",
  "end",
] as const

// Where each node is dropped, as a point on the canvas. Start goes first:
// the canvas centres the first node it receives, and the rest are placed
// around that spot, clear of the settings panel on the right.
const DROP_AT: Record<(typeof OFFERED)[number], { x: number; y: number }> = {
  start: { x: 660, y: 480 },
  llm: { x: 130, y: 200 },
  conditionBranch: { x: 380, y: 200 },
  agent: { x: 630, y: 200 },
  knowledgeRetrieval: { x: 880, y: 200 },
  connector: { x: 880, y: 700 },
  mcp: { x: 630, y: 700 },
  end: { x: 380, y: 700 },
}

// Placeholder of every prompt / query template field.
const PROMPT_HINT = /for variable interpolation$/

let seeded: Seed

test.describe.configure({ mode: "serial" })

test.beforeAll(async ({ request }) => {
  seeded = await seed(request)
})

test.beforeEach(async ({ page }) => {
  await signIn(page, seeded.session)
})

/** Pick a variable in the picker that sits next to a field. */
async function insertVariable(page: Page, reference: string): Promise<void> {
  await page.getByRole("option", { name: reference }).click()
}

/** Choose an entry of the toolbar's "more" menu. */
async function chooseMenuItem(page: Page, name: string): Promise<void> {
  await page.getByTestId("workflow-more-menu").click()
  await page.getByRole("menuitem", { name, exact: true }).click()
  await expect(page.getByRole("menu")).toHaveCount(0)
}

async function saveWorkflow(page: Page): Promise<void> {
  const saved = page.waitForResponse(
    (res) => res.request().method() === "PUT" && /\/api\/workflows\/[^/]+$/.test(new URL(res.url()).pathname),
  )
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await saved).status()).toBe(200)
}

test("a new blank workflow opens in the editor with Start and End", async ({ page }) => {
  await page.goto("/workflows")
  await page.getByRole("button", { name: "New Workflow" }).click()
  await page.waitForURL(/\/workflows\/[0-9a-f-]{36}$/)
  await expect(canvasNodes(page)).toHaveCount(2)
  await expect(nodeOfType(page, "start")).toHaveCount(1)
  await expect(nodeOfType(page, "end")).toHaveCount(1)
})

test("build, save, reload and run a workflow that uses every offered node type", async ({
  page,
  request,
}) => {
  const workflowId = await createEmptyWorkflow(request, seeded.session, "Smoke workflow")
  await page.goto(`/workflows/${workflowId}`)
  await expect(page.locator(".react-flow__pane")).toBeVisible()
  await expect(canvasNodes(page)).toHaveCount(0)

  // --- Palette -------------------------------------------------------------
  await test.step("the palette offers eight node types and not the approval node", async () => {
    for (const type of OFFERED) await expect(paletteItem(page, type)).toBeVisible()
    await expect(paletteItem(page, "humanIntervention")).toHaveCount(0)
    await expect(page.getByText("Human Review")).toHaveCount(0)
  })

  // --- Add -----------------------------------------------------------------
  await test.step("add one node of each type", async () => {
    for (const type of OFFERED) await addNode(page, type, DROP_AT[type])
    await expect(canvasNodes(page)).toHaveCount(OFFERED.length)
    // Start and End are singletons: the palette stops offering a second one.
    await expect(paletteItem(page, "start")).toHaveAttribute("draggable", "false")
    await expect(paletteItem(page, "end")).toHaveAttribute("draggable", "false")
  })

  const ids = {
    start: await nodeId(page, "start"),
    llm: await nodeId(page, "llm"),
    agent: await nodeId(page, "agent"),
    kb: await nodeId(page, "knowledgeRetrieval"),
    connector: await nodeId(page, "connector"),
    mcp: await nodeId(page, "mcp"),
  }

  // --- Configure -----------------------------------------------------------
  await test.step("configure Start", async () => {
    const panel = await openConfig(page, "start")
    await panel.getByRole("button", { name: "Add variable" }).click()
    await panel.getByPlaceholder("Variable name").fill("topic")
  })

  await test.step("configure LLM, inserting a variable with the picker", async () => {
    const panel = await openConfig(page, "llm")
    const prompt = panel.getByPlaceholder(PROMPT_HINT)
    await prompt.fill("Write about ")
    await panel.getByRole("button", { name: "Insert variable reference" }).click()
    await insertVariable(page, `{{${ids.start}.topic}}`)
    await expect(prompt).toHaveValue(`Write about {{${ids.start}.topic}}`)

    // Sections every step shares: the note, Advanced, Notes.
    await panel.getByPlaceholder("Add a note...").fill("drafts the text")
    await panel.getByRole("button", { name: "Advanced" }).click()
    await panel.getByRole("spinbutton").nth(2).fill("2")
    await panel.getByRole("button", { name: "Notes" }).click()
    await panel.getByPlaceholder("Add a comment or annotation for this node...").fill("smoke comment")
    await expect(nodeOfType(page, "llm")).toContainText("drafts the text")
  })

  let conditionHandle = ""
  await test.step("configure Condition Branch", async () => {
    const panel = await openConfig(page, "conditionBranch")
    await panel.getByRole("button", { name: "Add condition" }).click()
    await panel.getByPlaceholder("Label").fill("has text")
    await panel.getByPlaceholder(/^Variable \(e\.g\./).fill(`{{${ids.llm}.llm_result}}`)
    await panel.getByRole("combobox").nth(1).click()
    await page.getByRole("option", { name: "contains", exact: true }).click()
    await panel.getByPlaceholder("Value").fill("STUB_REPLY")
    const handle = nodeOfType(page, "conditionBranch").locator(".react-flow__handle.source")
    await expect(handle).toHaveCount(1)
    conditionHandle = (await handle.getAttribute("data-handleid")) ?? ""
    expect(conditionHandle).toMatch(/^condition-/)
  })

  await test.step("configure Agent", async () => {
    const panel = await openConfig(page, "agent")
    const prompt = panel.getByPlaceholder(PROMPT_HINT)
    await prompt.fill("Summarise ")
    await panel.getByRole("button", { name: "Insert variable reference" }).click()
    await insertVariable(page, `{{${ids.llm}.llm_result}}`)
    await expect(prompt).toHaveValue(`Summarise {{${ids.llm}.llm_result}}`)
  })

  await test.step("configure Knowledge Retrieval", async () => {
    const panel = await openConfig(page, "knowledgeRetrieval")
    await panel.getByPlaceholder("KB ID").fill(seeded.kbId)
    await panel.getByPlaceholder(PROMPT_HINT).fill("sky colour")
  })

  await test.step("configure Connector", async () => {
    let connectorListCalls = 0
    page.on("request", (req) => {
      if (req.method() === "GET" && new URL(req.url()).pathname === "/api/connectors") {
        connectorListCalls += 1
      }
    })
    const panel = await openConfig(page, "connector")
    await panel.getByRole("combobox").nth(0).click()
    await page.getByRole("option", { name: seeded.connectorName }).click()
    await panel.getByRole("combobox").nth(1).click()
    await page.getByRole("option", { name: seeded.actionName }).click()
    await panel.getByRole("button", { name: "Add parameter" }).click()
    await panel.getByPlaceholder("Key").fill("q")
    await panel.getByPlaceholder("Value").fill(`{{${ids.start}.topic}}`)
    // The list is loaded when the form opens, not on every keystroke
    // (twice under React's development double-mount).
    expect(connectorListCalls).toBeLessThanOrEqual(2)
  })

  await test.step("configure MCP Tool", async () => {
    const panel = await openConfig(page, "mcp")
    await panel.getByPlaceholder("Server ID or name").fill(seeded.mcpServerId)
    await panel.getByPlaceholder("Tool to execute").fill("echo")
  })

  await test.step("configure End", async () => {
    const panel = await openConfig(page, "end")
    const outputs: Array<[string, string]> = [
      ["llm", `{{${ids.llm}.llm_result}}`],
      ["agent", `{{${ids.agent}.agent_result}}`],
      ["kb", `{{${ids.kb}.kb_result}}`],
      ["conn", `{{${ids.connector}.connector_result}}`],
      ["mcp", `{{${ids.mcp}.mcp_result}}`],
    ]
    for (const [key, reference] of outputs) {
      await panel.getByRole("button", { name: "Add mapping" }).click()
      await panel.getByPlaceholder("Key").last().fill(key)
      await panel.getByRole("button", { name: "Insert variable reference" }).last().click()
      await insertVariable(page, reference)
      await expect(panel.getByPlaceholder("Value").last()).toHaveValue(reference)
    }
  })

  // --- Connect -------------------------------------------------------------
  await test.step("connect the nodes into one path", async () => {
    await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } })
    await expect(configPanel(page)).toHaveCount(0)
    await connect(page, { type: "start" }, "llm")
    await connect(page, { type: "llm" }, "conditionBranch")
    await connect(page, { type: "conditionBranch", handle: conditionHandle }, "agent")
    await connect(page, { type: "agent" }, "knowledgeRetrieval")
    await connect(page, { type: "knowledgeRetrieval" }, "connector")
    await connect(page, { type: "connector" }, "mcp")
    await connect(page, { type: "mcp" }, "end")
    await expect(canvasEdges(page)).toHaveCount(7)
  })

  // --- Save ----------------------------------------------------------------
  await test.step("save", async () => {
    // Straight after the last edge: Save must include it, although the
    // canvas reports its changes to the page only after a short pause.
    await saveWorkflow(page)
  })
  const saved = await fetchBlueprint(request, seeded.session, workflowId)
  expect(saved.nodes.map((n) => n.type).sort()).toEqual([...OFFERED].sort())
  expect(saved.edges).toHaveLength(7)

  // --- Reload --------------------------------------------------------------
  await test.step("reload shows the same graph and settings", async () => {
    await page.reload()
    await expect(canvasNodes(page)).toHaveCount(OFFERED.length)
    await expect(canvasEdges(page)).toHaveCount(7)
    for (const type of OFFERED) await expect(nodeOfType(page, type)).toHaveCount(1)
    for (const node of saved.nodes) {
      await expect(page.locator(`.react-flow__node[data-id="${node.id}"]`)).toHaveCount(1)
    }

    const llm = await openConfig(page, "llm")
    await expect(llm.getByPlaceholder(PROMPT_HINT)).toHaveValue(
      `Write about {{${ids.start}.topic}}`,
    )
    await expect(llm.getByPlaceholder("Add a note...")).toHaveValue("drafts the text")
    const llmData = saved.nodes.find((n) => n.type === "llm")?.data
    expect(llmData).toMatchObject({ note: "drafts the text", comment: "smoke comment", retry_count: 2 })
    const connector = await openConfig(page, "connector")
    await expect(connector.getByRole("combobox").nth(0)).toContainText(seeded.connectorName)
    await expect(connector.getByRole("combobox").nth(1)).toContainText(seeded.actionName)
    const end = await openConfig(page, "end")
    await expect(end.getByPlaceholder("Key")).toHaveCount(5)

    // Opening settings without editing changes nothing: no unsaved marker,
    // and what is stored stays as it was.
    await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } })
    await page.waitForTimeout(500)
    await expect(page.getByText("Unsaved changes")).toHaveCount(0)
    expect(await fetchBlueprint(request, seeded.session, workflowId)).toEqual(saved)
  })

  // --- Run -----------------------------------------------------------------
  const modelCallsBefore = (await stubRequests(request)).filter(
    (r) => r.path === "/v1/chat/completions",
  ).length

  await test.step("run and read the result", async () => {
    await page.getByRole("button", { name: "Run", exact: true }).click()
    const runPanel = page.getByTestId("run-panel")
    await expect(runPanel).toBeVisible()
    await runPanel.getByRole("textbox").first().fill("otters")
    await runPanel.getByRole("button", { name: "Start Run" }).click()

    await expect(runPanel.getByText("Completed").first()).toBeVisible({ timeout: 60_000 })
    await expect(runPanel).toContainText("STUB_REPLY Write about otters")
    await expect(runPanel).toContainText("STUB_REPLY Summarise STUB_REPLY Write about otters")
    await expect(runPanel).toContainText(KB_FACT)
    await expect(runPanel).toContainText("MCP_STUB")
    await expect(runPanel).toContainText("otters")
    await expect(runPanel.getByText("Failed")).toHaveCount(0)

    // Steps are listed by name, and the progress counts all of them.
    await expect(runPanel.getByText("8/8 nodes")).toBeVisible()
    for (const name of ["Start", "LLM", "Condition Branch", "Agent", "Knowledge Retrieval", "Connector", "MCP Tool", "End"]) {
      await expect(runPanel.getByText(name, { exact: true }).first()).toBeVisible()
    }
  })

  await test.step("the model that answered was the stub", async () => {
    const calls = (await stubRequests(request)).filter((r) => r.path === "/v1/chat/completions")
    expect(calls.length).toBeGreaterThan(modelCallsBefore)
    expect(calls.some((c) => c.lastUser === "Write about otters")).toBe(true)
  })

  // --- Run history ---------------------------------------------------------
  await test.step("the run is listed in the run history", async () => {
    await chooseMenuItem(page, "Run History")
    const sheet = page.getByRole("dialog")
    await expect(sheet.getByText("Run History")).toBeVisible()
    await expect(sheet.getByText("Completed").first()).toBeVisible()

    // Replay the run on the canvas, then clear it.
    await sheet.getByText("Completed").first().click()
    await sheet.getByRole("button", { name: "View on Canvas" }).click()
    await expect(sheet).toHaveCount(0)
    await expect(page.getByText("Viewing past run")).toBeVisible()
    await page.getByRole("button", { name: "Clear" }).click()
    await expect(page.getByText("Viewing past run")).toHaveCount(0)
    // A replay is not an edit.
    await page.waitForTimeout(500)
    await expect(page.getByText("Unsaved changes")).toHaveCount(0)
  })

  // --- Export / duplicate --------------------------------------------------
  await test.step("export downloads the stored blueprint", async () => {
    const download = page.waitForEvent("download")
    await chooseMenuItem(page, "Export")
    const file = await (await download).path()
    const exported = JSON.parse(await readFile(file, "utf8")) as {
      workflow: { name: string; blueprint: typeof saved }
    }
    expect(exported.workflow.name).toBe("Smoke workflow")
    expect(exported.workflow.blueprint.nodes.map((n) => n.id).sort()).toEqual(
      saved.nodes.map((n) => n.id).sort(),
    )
    expect(exported.workflow.blueprint.edges).toHaveLength(7)
  })

  await test.step("duplicate opens a copy with the same graph", async () => {
    await chooseMenuItem(page, "Duplicate")
    await page.waitForURL((url) => /\/workflows\/[0-9a-f-]{36}$/.test(url.pathname) && !url.pathname.endsWith(workflowId))
    await expect(canvasNodes(page)).toHaveCount(OFFERED.length)
    await expect(canvasEdges(page)).toHaveCount(7)
    for (const type of OFFERED) await expect(nodeOfType(page, type)).toHaveCount(1)
  })
})

test("canvas editing: copy, paste, undo, redo, delete, search and the context menu", async ({
  page,
  request,
}) => {
  const workflowId = await createLinearWorkflow(request, seeded.session, "Editing workflow")
  await page.goto(`/workflows/${workflowId}`)
  await expect(canvasNodes(page)).toHaveCount(3)
  await expect(canvasEdges(page)).toHaveCount(2)
  // Undo steps are recorded 300 ms after a change.
  const settle = () => page.waitForTimeout(450)
  await settle()

  await test.step("copy and paste a node with the keyboard", async () => {
    await nodeOfType(page, "llm").click()
    await expect(configPanel(page)).toBeVisible()
    await page.keyboard.press("Control+c")
    await page.keyboard.press("Control+v")
    await expect(nodeOfType(page, "llm")).toHaveCount(2)
    await settle()
  })

  await test.step("undo and redo", async () => {
    await page.keyboard.press("Control+z")
    await expect(nodeOfType(page, "llm")).toHaveCount(1)
    await page.keyboard.press("Control+Shift+z")
    await expect(nodeOfType(page, "llm")).toHaveCount(2)
    await settle()
  })

  await test.step("delete the selection with the keyboard; Start cannot be deleted", async () => {
    await nodeOfType(page, "llm").last().click()
    await page.keyboard.press("Escape")
    await expect(configPanel(page)).toHaveCount(0)
    await nodeOfType(page, "llm").last().click()
    await page.keyboard.press("Backspace")
    await expect(nodeOfType(page, "llm")).toHaveCount(1)
    await settle()

    await nodeOfType(page, "start").click()
    await page.keyboard.press("Backspace")
    await expect(page.getByText("Cannot delete the Start node")).toBeVisible()
    await expect(canvasNodes(page)).toHaveCount(3)
  })

  await test.step("find on canvas", async () => {
    await page.keyboard.press("Control+f")
    const find = page.getByPlaceholder("Search nodes...").last()
    await find.fill("say")
    await expect(page.getByText("1 of 1")).toBeVisible()
    await find.fill("nothing-matches-this")
    await expect(page.getByText("No results")).toBeVisible()
    await find.press("Escape")
    await expect(page.getByText("No results")).toHaveCount(0)
  })

  await test.step("context menu: delete a node, then undo brings it back with its edges", async () => {
    await nodeOfType(page, "llm").click({ button: "right" })
    await page.getByRole("button", { name: "Delete", exact: true }).click()
    await expect(nodeOfType(page, "llm")).toHaveCount(0)
    await expect(canvasEdges(page)).toHaveCount(0)
    await settle()
    await page.keyboard.press("Control+z")
    await expect(nodeOfType(page, "llm")).toHaveCount(1)
    await expect(canvasEdges(page)).toHaveCount(2)
  })

  await test.step("context menu on the canvas: select all, then copy and paste the selection", async () => {
    await page.locator(".react-flow__pane").click({ button: "right", position: { x: 40, y: 500 } })
    await page.getByRole("button", { name: "Select All" }).click()
    await expect(page.locator(".react-flow__node.selected")).toHaveCount(3)
    await page.keyboard.press("Control+c")
    await page.keyboard.press("Control+v")
    // Start and End are singletons: only the LLM step is pasted.
    await expect(canvasNodes(page)).toHaveCount(4)
    await expect(nodeOfType(page, "llm")).toHaveCount(2)
    await expect(nodeOfType(page, "start")).toHaveCount(1)
  })
})

test("workflow variables survive opening and editing the canvas", async ({ page, request }) => {
  const region = { name: "region", type: "string", default_value: "eu", description: "" }
  const workflowId = await createLinearWorkflow(request, seeded.session, "Variables workflow", "Hello", [
    region,
  ])
  await page.goto(`/workflows/${workflowId}`)
  await expect(canvasNodes(page)).toHaveCount(3)

  // Opening is not an edit.
  await page.waitForTimeout(800)
  await expect(page.getByText("Unsaved changes")).toHaveCount(0)

  const panel = await openConfig(page, "llm")
  await panel.getByPlaceholder(PROMPT_HINT).fill("Hello again")
  await saveWorkflow(page)

  const stored = await fetchBlueprint(request, seeded.session, workflowId)
  expect(stored.nodes.find((n) => n.type === "llm")?.data.prompt_template).toBe("Hello again")
  expect(stored.variables).toEqual([region])
})

test("restoring a version and importing a file replace what is on the canvas", async ({
  page,
  request,
}, testInfo) => {
  const workflowId = await createLinearWorkflow(request, seeded.session, "Versions workflow", "first")
  await page.goto(`/workflows/${workflowId}`)
  await expect(canvasNodes(page)).toHaveCount(3)

  // Two saves, two versions: "second", then "third".
  for (const prompt of ["second", "third"]) {
    const panel = await openConfig(page, "llm")
    await panel.getByPlaceholder(PROMPT_HINT).fill(prompt)
    await saveWorkflow(page)
  }
  await expect(nodeOfType(page, "llm")).toContainText("third")

  await test.step("restore the earlier version", async () => {
    await page.getByTestId("workflow-more-menu").click()
    await page.getByRole("menuitem", { name: "Version History" }).click()
    const sheet = page.getByRole("dialog")
    await sheet.getByRole("button", { name: "Restore" }).last().click()
    await page.getByRole("alertdialog").getByRole("button", { name: "Restore" }).click()
    await expect(page.getByText("Version restored successfully")).toBeVisible()

    // The canvas shows the restored graph, and so does the settings panel.
    await expect(nodeOfType(page, "llm")).toContainText("second")
    const panel = await openConfig(page, "llm")
    await expect(panel.getByPlaceholder(PROMPT_HINT)).toHaveValue("second")

    // Touching the canvas afterwards does not put the old graph back.
    await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } })
    await page.waitForTimeout(800)
    await expect(page.getByText("Unsaved changes")).toHaveCount(0)
    const stored = await fetchBlueprint(request, seeded.session, workflowId)
    expect(stored.nodes.find((n) => n.type === "llm")?.data.prompt_template).toBe("second")
  })

  await test.step("import a file", async () => {
    const file = testInfo.outputPath("import.json")
    await writeFile(
      file,
      JSON.stringify({
        workflow: {
          name: "Imported",
          blueprint: {
            schema_version: 2,
            nodes: [
              { id: "start_1", type: "start", position: { x: 100, y: 100 }, data: { variables: [] } },
              {
                id: "agent_9",
                type: "agent",
                position: { x: 420, y: 100 },
                data: { agent_id: "", prompt_template: "from the file", output_variable: "agent_result" },
              },
              { id: "end_1", type: "end", position: { x: 740, y: 100 }, data: { output_mapping: {} } },
            ],
            edges: [
              { id: "i1", source: "start_1", target: "agent_9", sourceHandle: "source", targetHandle: "target" },
            ],
            viewport: { x: 0, y: 0, zoom: 1 },
          },
        },
      }),
    )
    await page.getByTestId("workflow-more-menu").click()
    const chooser = page.waitForEvent("filechooser")
    await page.getByRole("menuitem", { name: "Import", exact: true }).click()
    await (await chooser).setFiles(file)

    await expect(page.getByText("Workflow imported")).toBeVisible()
    await expect(nodeOfType(page, "agent")).toHaveCount(1)
    await expect(nodeOfType(page, "llm")).toHaveCount(0)
    await expect(canvasEdges(page)).toHaveCount(1)
    await expect(page.getByText("Unsaved changes")).toBeVisible()

    await saveWorkflow(page)
    const stored = await fetchBlueprint(request, seeded.session, workflowId)
    expect(stored.nodes.map((n) => n.id).sort()).toEqual(["agent_9", "end_1", "start_1"])
  })
})
