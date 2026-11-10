import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"
import type { ReactElement } from "react"

import workflows from "../../../../../messages/en/workflows.json"
import errors from "../../../../../messages/en/errors.json"
import { defaultNodeData } from "@/lib/workflow-blueprint"
import { WORKFLOW_NODE_V2_TYPES } from "@/types/workflow-blueprint.generated"
import { formForNodeType, NODE_FORMS } from "../registry"

/**
 * Every node type has one settings form, and each form writes the fields
 * the blueprint v2 schema names. The smoke cannot reach the approval form
 * while its switch is off, so it is exercised here.
 */

const api = vi.hoisted(() => ({
  connectors: [] as Array<Record<string, unknown>>,
}))

vi.mock("@/lib/api", () => {
  const none = () => Promise.resolve({ items: [], pages: 1 })
  return {
    connectorApi: {
      list: vi.fn(() => Promise.resolve({ items: api.connectors, pages: 1 })),
    },
    agentApi: { list: vi.fn(none) },
    kbApi: { list: vi.fn(none) },
    mcpServerApi: { list: vi.fn(none) },
    workflowApi: { listStepMcpTools: vi.fn(() => Promise.resolve([])) },
  }
})

vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ user: { id: "u1" } }),
}))

beforeAll(() => {
  // Radix (Slider, Popover) measures with ResizeObserver, which jsdom lacks.
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub)
})

afterEach(() => {
  cleanup()
  api.connectors = []
})

function renderWithMessages(ui: ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }}>
      {ui}
    </NextIntlClientProvider>,
  )
}

function renderForm(type: (typeof WORKFLOW_NODE_V2_TYPES)[number], data = defaultNodeData(type)) {
  const Form = formForNodeType(type)
  if (!Form) throw new Error(`no form for ${type}`)
  const setField = vi.fn()
  const setFields = vi.fn()
  renderWithMessages(<Form data={data} setField={setField} setFields={setFields} otherNodes={[]} />)
  return { setField, setFields }
}

describe("node settings forms", () => {
  it("has exactly one form per node type of the schema", () => {
    expect(Object.keys(NODE_FORMS).sort()).toEqual([...WORKFLOW_NODE_V2_TYPES].sort())
  })

  it("has no form for a node type the editor does not know", () => {
    expect(formForNodeType("webhook")).toBeNull()
    expect(formForNodeType("toString")).toBeNull()
    expect(formForNodeType(undefined)).toBeNull()
  })

  it.each(WORKFLOW_NODE_V2_TYPES)("renders the %s form from a new node's data", (type) => {
    const { setField, setFields } = renderForm(type)
    expect(setField).not.toHaveBeenCalled()
    expect(setFields).not.toHaveBeenCalled()
  })

  it("LLM: the prompt fields write prompt_template and system_prompt", () => {
    const { setField } = renderForm("llm")
    fireEvent.change(screen.getByPlaceholderText(workflows.configSystemPromptHint), {
      target: { value: "be brief" },
    })
    expect(setField).toHaveBeenLastCalledWith("system_prompt", "be brief")
    fireEvent.change(screen.getByPlaceholderText(/for variable interpolation$/), {
      target: { value: "hello" },
    })
    expect(setField).toHaveBeenLastCalledWith("prompt_template", "hello")
  })

  it("LLM: max tokens is a number, and empty clears it", () => {
    const { setField } = renderForm("llm")
    const maxTokens = screen.getByPlaceholderText("4096")
    fireEvent.change(maxTokens, { target: { value: "256" } })
    expect(setField).toHaveBeenLastCalledWith("max_tokens", 256)
    cleanup()
    const second = renderForm("llm", { ...defaultNodeData("llm"), max_tokens: 256 })
    fireEvent.change(screen.getByPlaceholderText("4096"), { target: { value: "" } })
    expect(second.setField).toHaveBeenLastCalledWith("max_tokens", undefined)
  })

  it("Start: adding a variable appends an optional string variable", () => {
    const { setField } = renderForm("start")
    fireEvent.click(screen.getByRole("button", { name: workflows.configAddVariable }))
    expect(setField).toHaveBeenLastCalledWith("variables", [
      { name: "", type: "string", required: false },
    ])
  })

  it("Start: renaming a variable keeps the others", () => {
    const { setField } = renderForm("start", {
      variables: [
        { name: "a", type: "string", required: true },
        { name: "b", type: "number", required: false },
      ],
    })
    fireEvent.change(screen.getAllByPlaceholderText(workflows.configVariableName)[1], {
      target: { value: "count" },
    })
    expect(setField).toHaveBeenLastCalledWith("variables", [
      { name: "a", type: "string", required: true },
      { name: "count", type: "number", required: false },
    ])
  })

  it("End: renaming a mapping key keeps its value and the order", () => {
    const { setField } = renderForm("end", { output_mapping: { first: "{{a.x}}", second: "{{b.y}}" } })
    fireEvent.change(screen.getAllByPlaceholderText(workflows.configKey)[0], {
      target: { value: "renamed" },
    })
    const mapping = setField.mock.calls.at(-1)?.[1] as Record<string, string>
    expect(setField.mock.calls.at(-1)?.[0]).toBe("output_mapping")
    expect(Object.entries(mapping)).toEqual([
      ["renamed", "{{a.x}}"],
      ["second", "{{b.y}}"],
    ])
  })

  it("Condition Branch: a new condition compares with ==", () => {
    const { setField } = renderForm("conditionBranch")
    fireEvent.click(screen.getByRole("button", { name: workflows.configAddCondition }))
    const [field, conditions] = setField.mock.calls.at(-1) as [string, Array<Record<string, unknown>>]
    expect(field).toBe("conditions")
    expect(conditions).toHaveLength(1)
    expect(conditions[0]).toMatchObject({ label: "", variable: "", operator: "==", value: "" })
    expect(String(conditions[0].id)).toMatch(/^cond_\d+$/)
  })

  it("MCP: the tool name field writes tool_name", () => {
    const mcp = renderForm("mcp")
    fireEvent.change(screen.getByPlaceholderText(workflows.configToolNamePlaceholder), {
      target: { value: "echo" },
    })
    expect(mcp.setField).toHaveBeenLastCalledWith("tool_name", "echo")
  })

  it("Human Review: writes the review prompt, assignee and timeout", () => {
    const { setField } = renderForm("humanIntervention")
    fireEvent.change(screen.getByPlaceholderText(workflows.configPromptMessagePlaceholder), {
      target: { value: "check the draft" },
    })
    expect(setField).toHaveBeenLastCalledWith("prompt_message", "check the draft")
    fireEvent.change(screen.getByPlaceholderText(workflows.configAssigneePlaceholder), {
      target: { value: "ada@example.com" },
    })
    expect(setField).toHaveBeenLastCalledWith("assignee", "ada@example.com")
    fireEvent.change(screen.getByDisplayValue("24"), { target: { value: "48" } })
    expect(setField).toHaveBeenLastCalledWith("timeout_hours", 48)
  })

  // A Connector step pointed at a database connector had nothing to fill in
  // but an action it does not have. It now offers the connector's operations.
  const warehouse = { id: "c-db", name: "Warehouse", type: "database", is_active: true, user_id: "u1", actions: [] }

  it("Connector: a database connector offers an operation and a SQL field for a query", async () => {
    api.connectors = [warehouse]
    const { setField } = renderForm("connector", {
      ...defaultNodeData("connector"),
      connector_id: "c-db",
      action_id: "query",
      parameters: { sql: "SELECT 1" },
    })
    expect(await screen.findByText(workflows.configSelectOperation)).toBeTruthy()
    expect(screen.queryByText(workflows.configSelectAction)).toBeNull()
    expect(screen.queryByRole("button", { name: workflows.configAddParam })).toBeNull()
    const sql = screen.getByDisplayValue("SELECT 1")
    fireEvent.change(sql, { target: { value: "SELECT id FROM orders" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { sql: "SELECT id FROM orders" })
  })

  it("Connector: describing a table asks for the table name only", async () => {
    api.connectors = [warehouse]
    const { setField } = renderForm("connector", {
      ...defaultNodeData("connector"),
      connector_id: "c-db",
      action_id: "describe_table",
      parameters: { table_name: "" },
    })
    const table = await screen.findByPlaceholderText(workflows.configTableNamePlaceholder)
    fireEvent.change(table, { target: { value: "orders" } })
    expect(setField).toHaveBeenLastCalledWith("parameters", { table_name: "orders" })
    expect(screen.queryByText(workflows.configSql)).toBeNull()
  })

  it("Connector: an API connector keeps its action and parameter fields", async () => {
    api.connectors = [{ id: "c-crm", name: "CRM", type: "api", is_active: true, user_id: "u1", actions: [] }]
    renderForm("connector", { ...defaultNodeData("connector"), connector_id: "c-crm" })
    expect(await screen.findByText(workflows.configSelectAction)).toBeTruthy()
    expect(screen.queryByText(workflows.configSelectOperation)).toBeNull()
  })

  it("Connector: adding a parameter keeps the existing ones", () => {
    const { setField } = renderForm("connector", {
      ...defaultNodeData("connector"),
      parameters: { q: "{{start_1.topic}}" },
    })
    fireEvent.click(screen.getByRole("button", { name: workflows.configAddParam }))
    expect(setField).toHaveBeenLastCalledWith("parameters", { q: "{{start_1.topic}}", "": "" })
  })
})
