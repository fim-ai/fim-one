import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"
import type { ReactElement, ReactNode } from "react"

import workflows from "../../../../../messages/en/workflows.json"
import errors from "../../../../../messages/en/errors.json"
import { defaultNodeData } from "@/lib/workflow-blueprint"
import type { WorkflowNodeV2Type } from "@/types/workflow-blueprint.generated"
import { formForNodeType } from "../registry"
import { listAllPages, resourceOptions } from "../resource-options"

/**
 * The Agent, Knowledge Retrieval and MCP steps took an id typed by hand.
 * Each now has a dropdown of what the author can use, and stores the same
 * field as before (`agent_id`, a one-element `kb_ids`, `server_id`).
 *
 * The dropdown component is replaced by a native <select> here: what the
 * forms offer and what they write is under test, not the popover.
 */

const listed = vi.hoisted(() => ({
  agents: [] as Array<Record<string, unknown>>,
  kbs: [] as Array<Record<string, unknown>>,
  servers: [] as Array<Record<string, unknown>>,
}))

vi.mock("@/lib/api", () => ({
  connectorApi: { list: vi.fn(() => Promise.resolve({ items: [], pages: 1 })) },
  agentApi: { list: vi.fn(() => Promise.resolve({ items: listed.agents, pages: 1 })) },
  kbApi: { list: vi.fn(() => Promise.resolve({ items: listed.kbs, pages: 1 })) },
  mcpServerApi: { list: vi.fn(() => Promise.resolve({ items: listed.servers, pages: 1 })) },
}))

vi.mock("@/contexts/auth-context", () => ({
  useAuth: () => ({ user: { id: "u1" } }),
}))

vi.mock("@/components/ui/select", () => ({
  Select: ({
    value,
    onValueChange,
    children,
  }: {
    value: string
    onValueChange: (v: string) => void
    children: ReactNode
  }) => (
    <select value={value} onChange={(e) => onValueChange(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
  SelectContent: ({ children }: { children: ReactNode }) => <>{children}</>,
  SelectItem: ({ value, children }: { value: string; children: ReactNode }) => (
    <option value={value}>{children}</option>
  ),
}))

afterEach(() => {
  cleanup()
  listed.agents = []
  listed.kbs = []
  listed.servers = []
})

function renderWithMessages(ui: ReactElement) {
  return render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }}>
      {ui}
    </NextIntlClientProvider>,
  )
}

function renderForm<K extends WorkflowNodeV2Type>(type: K, data: Record<string, unknown> = {}) {
  const Form = formForNodeType(type)
  if (!Form) throw new Error(`no form for ${type}`)
  const setField = vi.fn()
  const setFields = vi.fn()
  renderWithMessages(
    <Form
      data={{ ...defaultNodeData(type), ...data }}
      setField={setField}
      setFields={setFields}
      otherNodes={[]}
    />,
  )
  return { setField, setFields }
}

/** The option labels of the form's first dropdown, once `label` is among them. */
async function optionLabels(label: string): Promise<string[]> {
  await screen.findByRole("option", { name: label })
  const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement
  return Array.from(select.options).map((o) => o.textContent ?? "")
}

describe("resourceOptions", () => {
  const on = { id: "r-on", name: "On", is_active: true }
  const off = { id: "r-off", name: "Off", is_active: false }

  it("lists what is switched on, by name", () => {
    expect(resourceOptions([on, off], "")).toEqual([{ value: "r-on", label: "On" }])
  })

  it("keeps a switched-off resource the step already uses, by name", () => {
    expect(resourceOptions([on, off], "r-off")).toContainEqual({ value: "r-off", label: "Off" })
  })

  it("keeps a resource that is not in the list, by id", () => {
    expect(resourceOptions([on], "r-gone")).toContainEqual({ value: "r-gone", label: "r-gone" })
    expect(resourceOptions([], "r-gone")).toEqual([{ value: "r-gone", label: "r-gone" }])
  })
})

describe("listAllPages", () => {
  it("loads every page, 100 at a time", async () => {
    const list = vi.fn((page: number) =>
      Promise.resolve({ items: [`item-${page}`], pages: 3 }),
    )
    expect(await listAllPages(list)).toEqual(["item-1", "item-2", "item-3"])
    expect(list.mock.calls).toEqual([
      [1, 100],
      [2, 100],
      [3, 100],
    ])
  })
})

describe("Agent step", () => {
  it("offers the agents the author can use and writes agent_id", async () => {
    listed.agents = [
      { id: "a-1", name: "Researcher", is_active: true },
      { id: "a-2", name: "Retired", is_active: false },
      { id: "a-3", name: "Agent builder", is_active: true, is_builder: true },
    ]
    const { setField } = renderForm("agent")
    expect(await optionLabels("Researcher")).toEqual([workflows.configAgentNone, "Researcher"])
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "a-1" } })
    expect(setField).toHaveBeenLastCalledWith("agent_id", "a-1")
  })

  it("can go back to no agent", async () => {
    listed.agents = [{ id: "a-1", name: "Researcher", is_active: true }]
    const { setField } = renderForm("agent", { agent_id: "a-1" })
    await screen.findByRole("option", { name: "Researcher" })
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "__default__" } })
    expect(setField).toHaveBeenLastCalledWith("agent_id", "")
  })

  it("keeps showing an agent that is no longer listed", async () => {
    renderForm("agent", { agent_id: "a-gone" })
    expect(await optionLabels("a-gone")).toEqual([workflows.configAgentNone, "a-gone"])
  })
})

describe("Knowledge Retrieval step", () => {
  it("offers the author's own knowledge bases and writes a one-element kb_ids", async () => {
    listed.kbs = [
      { id: "kb-1", name: "Handbook", is_active: true, user_id: "u1" },
      { id: "kb-2", name: "Someone else's", is_active: true, user_id: "u2" },
      { id: "kb-3", name: "Switched off", is_active: false, user_id: "u1" },
    ]
    const { setField } = renderForm("knowledgeRetrieval")
    expect(await optionLabels("Handbook")).toEqual([workflows.configKBPlaceholder, "Handbook"])
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "kb-1" } })
    expect(setField).toHaveBeenLastCalledWith("kb_ids", ["kb-1"])
  })

  it("clearing the choice writes an empty list", async () => {
    listed.kbs = [{ id: "kb-1", name: "Handbook", is_active: true, user_id: "u1" }]
    const { setField } = renderForm("knowledgeRetrieval", { kb_ids: ["kb-1"] })
    await screen.findByRole("option", { name: "Handbook" })
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "__default__" } })
    expect(setField).toHaveBeenLastCalledWith("kb_ids", [])
  })
})

describe("MCP step", () => {
  it("offers the MCP servers the author can use", async () => {
    listed.servers = [
      { id: "s-1", name: "Files", is_active: true },
      { id: "s-2", name: "Switched off", is_active: false },
    ]
    renderForm("mcp")
    expect(await optionLabels("Files")).toEqual([workflows.configServerPlaceholder, "Files"])
  })

  it("choosing another server clears the tool and its arguments", async () => {
    listed.servers = [
      { id: "s-1", name: "Files", is_active: true },
      { id: "s-2", name: "Search", is_active: true },
    ]
    const { setFields } = renderForm("mcp", {
      server_id: "s-1",
      tool_name: "read",
      parameters: { path: "/tmp/a" },
    })
    await screen.findByRole("option", { name: "Search" })
    fireEvent.change(screen.getAllByRole("combobox")[0], { target: { value: "s-2" } })
    expect(setFields).toHaveBeenLastCalledWith({ server_id: "s-2", tool_name: "", parameters: {} })
  })
})
