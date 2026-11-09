import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"
import type { Node } from "@xyflow/react"

import workflows from "../../../../messages/en/workflows.json"
import { defaultNodeData } from "@/lib/workflow-blueprint"
import { WORKFLOW_NODE_V2_TYPES } from "@/types/workflow-blueprint.generated"
import { getNodeOutputVariables, VariablePicker } from "@/components/workflows/variable-picker"

function node(id: string, type: string, data: Record<string, unknown> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data }
}

describe("getNodeOutputVariables", () => {
  it("lists a Start node's named variables", () => {
    const start = node("start_1", "start", {
      variables: [{ name: "topic", type: "string" }, { name: "", type: "string" }, { name: "count" }],
    })
    expect(getNodeOutputVariables(start)).toEqual([
      { nodeId: "start_1", nodeType: "start", variableName: "topic", reference: "{{start_1.topic}}" },
      { nodeId: "start_1", nodeType: "start", variableName: "count", reference: "{{start_1.count}}" },
    ])
  })

  it.each(["llm", "agent", "knowledgeRetrieval", "connector", "mcp", "humanIntervention"])(
    "lists the output variable of a %s node",
    (type) => {
      expect(getNodeOutputVariables(node("n1", type, { output_variable: "result" }))).toEqual([
        { nodeId: "n1", nodeType: type, variableName: "result", reference: "{{n1.result}}" },
      ])
    },
  )

  it("lists nothing for a node whose output variable is empty or unset", () => {
    expect(getNodeOutputVariables(node("n1", "llm", { output_variable: "" }))).toEqual([])
    expect(getNodeOutputVariables(node("n1", "llm"))).toEqual([])
    expect(getNodeOutputVariables(node("s", "start"))).toEqual([])
  })

  it("lists nothing for End and Condition Branch, which produce no variable", () => {
    expect(getNodeOutputVariables(node("e", "end", { output_mapping: { a: "b" } }))).toEqual([])
    expect(getNodeOutputVariables(node("c", "conditionBranch", { output_variable: "x" }))).toEqual([])
  })

  it("a new node of every type offers what its default settings name", () => {
    const offered = Object.fromEntries(
      WORKFLOW_NODE_V2_TYPES.map((type) => [
        type,
        getNodeOutputVariables(node(`${type}_1`, type, defaultNodeData(type))).map((v) => v.reference),
      ]),
    )
    expect(offered).toEqual({
      start: [],
      end: [],
      llm: ["{{llm_1.llm_result}}"],
      conditionBranch: [],
      agent: ["{{agent_1.agent_result}}"],
      knowledgeRetrieval: ["{{knowledgeRetrieval_1.kb_result}}"],
      connector: ["{{connector_1.connector_result}}"],
      humanIntervention: ["{{humanIntervention_1.approval_result}}"],
      mcp: ["{{mcp_1.mcp_result}}"],
    })
  })
})

describe("VariablePicker", () => {
  beforeAll(() => {
    // Radix Popover and cmdk measure and scroll, which jsdom does not implement.
    class ResizeObserverStub {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverStub)
    Element.prototype.scrollIntoView = () => {}
  })

  afterEach(cleanup)

  function renderPicker(sourceNodes: Node[]) {
    const onInsert = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={{ workflows }}>
        <VariablePicker sourceNodes={sourceNodes} onInsert={onInsert} />
      </NextIntlClientProvider>,
    )
    return onInsert
  }

  const sources = [
    node("start_1", "start", { variables: [{ name: "topic" }, { name: "tone" }] }),
    node("llm_1", "llm", { output_variable: "draft" }),
    node("end_1", "end", { output_mapping: {} }),
  ]

  const open = () =>
    fireEvent.click(screen.getByRole("button", { name: workflows.variablePickerLabel }))

  it("is closed until its button is pressed", () => {
    renderPicker(sources)
    expect(screen.queryByText("{{start_1.topic}}")).toBeNull()
  })

  it("lists the variables of the other nodes, grouped by node", () => {
    renderPicker(sources)
    open()
    expect(screen.getByText("{{start_1.topic}}")).toBeDefined()
    expect(screen.getByText("{{start_1.tone}}")).toBeDefined()
    expect(screen.getByText("{{llm_1.draft}}")).toBeDefined()
    // Group headings: the node's type and id. End offers nothing and has no group.
    expect(screen.getByText("start_1")).toBeDefined()
    expect(screen.getByText("llm_1")).toBeDefined()
    expect(screen.queryByText("end_1")).toBeNull()
    expect(screen.getAllByRole("option")).toHaveLength(3)
  })

  it("inserts the chosen reference and closes", () => {
    const onInsert = renderPicker(sources)
    open()
    fireEvent.click(screen.getByText("{{llm_1.draft}}"))
    expect(onInsert).toHaveBeenCalledTimes(1)
    expect(onInsert).toHaveBeenCalledWith("{{llm_1.draft}}")
    expect(screen.queryByText("{{llm_1.draft}}")).toBeNull()
  })

  it("filters by what is typed", () => {
    renderPicker(sources)
    open()
    fireEvent.change(screen.getByPlaceholderText(workflows.variablePickerSearch), {
      target: { value: "draft" },
    })
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["{{llm_1.draft}}"])
  })

  it("says so when no other node offers a variable", () => {
    renderPicker([node("end_1", "end", {})])
    open()
    expect(screen.getByText(workflows.variablePickerEmpty)).toBeDefined()
    expect(screen.queryAllByRole("option")).toHaveLength(0)
  })
})
