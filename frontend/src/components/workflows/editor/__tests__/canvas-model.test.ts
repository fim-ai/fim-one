import { describe, expect, it } from "vitest"
import type { Edge, Node } from "@xyflow/react"

import {
  blueprintEdges,
  blueprintNodes,
  copySelection,
  edgeId,
  findSearchMatches,
  highlightSearch,
  isConnectionAllowed,
  nodeTypesOnCanvas,
  pasteClipboard,
  runOverlayOf,
  validationByNode,
  withLiveRunStatus,
  withRunAnimation,
  withRunOverlay,
  withValidationState,
  withoutRunOverlay,
  withoutValidationState,
} from "@/components/workflows/editor/canvas-model"
import type { NodeRunResult, WorkflowBlueprint } from "@/types/workflow"

function node(id: string, type: string, data: Record<string, unknown> = {}, extra: Partial<Node> = {}): Node {
  return { id, type, position: { x: 0, y: 0 }, data, ...extra }
}

function edge(source: string, target: string, extra: Partial<Edge> = {}): Edge {
  return { id: `e-${source}-${target}`, source, target, ...extra }
}

function result(status: NodeRunResult["status"], extra: Partial<NodeRunResult> = {}): NodeRunResult {
  return {
    status,
    output: null,
    error: null,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    ...extra,
  }
}

const start = node("start_1", "start", { variables: [{ name: "topic", type: "string" }] })
const llm = node("llm_1", "llm", { prompt_template: "Write about {{start_1.topic}}", output_variable: "draft" })
const end = node("end_1", "end", { output_mapping: {} })

describe("blueprint to canvas", () => {
  const blueprint: WorkflowBlueprint = {
    schema_version: 2,
    nodes: [{ id: "llm_1", type: "llm", position: { x: 5, y: 6 }, data: { prompt_template: "p" } }],
    edges: [
      { id: "e1", source: "a", target: "b", sourceHandle: "source", targetHandle: "target" },
      { id: "e2", source: "b", target: "c" },
    ],
    viewport: { x: 0, y: 0, zoom: 1 },
  }

  it("keeps id, type, position and data of each node", () => {
    expect(blueprintNodes(blueprint)).toEqual([
      { id: "llm_1", type: "llm", position: { x: 5, y: 6 }, data: { prompt_template: "p" } },
    ])
  })

  it("keeps edge handles and leaves missing ones undefined", () => {
    const edges = blueprintEdges(blueprint)
    expect(edges[0]).toMatchObject({ sourceHandle: "source", targetHandle: "target" })
    expect(edges[1].sourceHandle).toBeUndefined()
    expect(edges[1].targetHandle).toBeUndefined()
  })
})

describe("isConnectionAllowed", () => {
  const nodes = [start, llm, end]

  it("allows a new edge between two different nodes", () => {
    expect(isConnectionAllowed(nodes, [], { source: "start_1", target: "llm_1" })).toBe(true)
  })

  it("refuses a self-connection, an edge into Start and an edge out of End", () => {
    expect(isConnectionAllowed(nodes, [], { source: "llm_1", target: "llm_1" })).toBe(false)
    expect(isConnectionAllowed(nodes, [], { source: "llm_1", target: "start_1" })).toBe(false)
    expect(isConnectionAllowed(nodes, [], { source: "end_1", target: "llm_1" })).toBe(false)
  })

  it("refuses a duplicate edge but allows the same pair through another handle", () => {
    const edges = [edge("cond_1", "llm_1", { sourceHandle: "condition-a", targetHandle: "target" })]
    const all = [...nodes, node("cond_1", "conditionBranch")]
    expect(
      isConnectionAllowed(all, edges, {
        source: "cond_1",
        target: "llm_1",
        sourceHandle: "condition-a",
        targetHandle: "target",
      }),
    ).toBe(false)
    expect(
      isConnectionAllowed(all, edges, {
        source: "cond_1",
        target: "llm_1",
        sourceHandle: "condition-b",
        targetHandle: "target",
      }),
    ).toBe(true)
  })

  it("names an edge after its endpoints and handles", () => {
    expect(edgeId("a", "source", "b", "target")).toBe("e-a-source-b-target")
    expect(edgeId("a", null, "b", undefined)).toBe("e-a-default-b-default")
  })
})

describe("copy and paste", () => {
  const a = node("llm_1", "llm", { prompt_template: "p" }, { selected: true, position: { x: 10, y: 20 } })
  const b = node("agent_1", "agent", {}, { selected: true })
  const c = node("mcp_1", "mcp")

  it("copies the selected nodes and only the edges between them", () => {
    const clip = copySelection([a, b, c], [edge("llm_1", "agent_1"), edge("agent_1", "mcp_1")])
    expect(clip?.nodes.map((n) => n.id)).toEqual(["llm_1", "agent_1"])
    expect(clip?.edges.map((e) => e.id)).toEqual(["e-llm_1-agent_1"])
    expect(clip?.nodes[0].data).not.toBe(a.data)
  })

  it("copies nothing when nothing is selected", () => {
    expect(copySelection([c], [])).toBeNull()
  })

  it("pastes copies under new ids, offset and selected, with their edges rewired", () => {
    const clip = { nodes: [a, b], edges: [edge("llm_1", "agent_1", { sourceHandle: "source" })] }
    const pasted = pasteClipboard(clip, 1700)
    expect(pasted.nodes.map((n) => n.id)).toEqual(["llm_1700_0", "agent_1700_1"])
    expect(pasted.nodes[0].position).toEqual({ x: 60, y: 70 })
    expect(pasted.nodes.every((n) => n.selected)).toBe(true)
    expect(pasted.edges).toEqual([
      expect.objectContaining({
        id: "e-llm_1700_0-source-agent_1700_1-default",
        source: "llm_1700_0",
        target: "agent_1700_1",
      }),
    ])
  })

  it("does not paste Start or End, nor the edges that touched them", () => {
    const clip = {
      nodes: [{ ...start, selected: true }, a, { ...end, selected: true }],
      edges: [edge("start_1", "llm_1"), edge("llm_1", "end_1")],
    }
    const pasted = pasteClipboard(clip, 1)
    expect(pasted.nodes.map((n) => n.type)).toEqual(["llm"])
    expect(pasted.edges).toEqual([])
    expect(pasteClipboard({ nodes: [start, end], edges: [] }, 1)).toEqual({ nodes: [], edges: [] })
  })
})

describe("findSearchMatches", () => {
  const nodes = [
    start,
    llm,
    node("agent_1", "agent", { prompt_template: "Summarise", runStatus: "draft", _runOverlay: { x: "draft" } }),
  ]

  it("matches nothing for a blank query", () => {
    expect(findSearchMatches(nodes, "  ")).toEqual([])
  })

  it("matches by node type, id and text settings, ignoring case", () => {
    expect(findSearchMatches(nodes, "LLM").map((n) => n.id)).toEqual(["llm_1"])
    expect(findSearchMatches(nodes, "start_1").map((n) => n.id)).toEqual(["start_1", "llm_1"])
    expect(findSearchMatches(nodes, "summarise").map((n) => n.id)).toEqual(["agent_1"])
  })

  it("looks inside lists of objects (Start variables)", () => {
    expect(findSearchMatches([start], "topic").map((n) => n.id)).toEqual(["start_1"])
  })

  it("does not search run-time overlay fields", () => {
    expect(findSearchMatches(nodes, "draft").map((n) => n.id)).toEqual(["llm_1"])
  })
})

describe("highlightSearch", () => {
  const nodes = [start, llm, end]

  it("rings the matches, marks the current one, and dims the rest", () => {
    const shown = highlightSearch(nodes, {
      active: true,
      matchIds: new Set(["start_1", "llm_1"]),
      currentId: "llm_1",
    })
    expect(shown[0].className).toContain("ring-primary/40")
    expect(shown[1].className).toContain("ring-offset-1")
    expect(shown[2].style).toEqual({ opacity: 0.25 })
  })

  it("removes the highlight once the search ends, and leaves clean nodes alone", () => {
    const highlighted = highlightSearch(nodes, { active: true, matchIds: new Set(), currentId: null })
    const cleared = highlightSearch(highlighted, { active: false, matchIds: new Set(), currentId: null })
    expect(cleared.every((n) => n.className === undefined && n.style === undefined)).toBe(true)
    const untouched = highlightSearch(nodes, { active: false, matchIds: new Set(), currentId: null })
    expect(untouched[0]).toBe(nodes[0])
  })
})

describe("run results on the canvas", () => {
  it("shortens long previews and keeps short ones", () => {
    const overlay = runOverlayOf(
      result("completed", { duration_ms: 12, output: "x".repeat(200), input_preview: { a: 1 } }),
    )
    expect(overlay.durationMs).toBe(12)
    expect(overlay.outputPreview).toBe("x".repeat(120) + "...")
    expect(overlay.inputPreview).toBe('{"a":1}')
    expect(overlay.runError).toBeNull()
  })

  it("stamps a past run on the nodes it covers and can remove it again", () => {
    const stamped = withRunOverlay([start, llm], { llm_1: result("failed", { error: "boom" }) })
    expect(stamped[0]).toBe(start)
    expect(stamped[1].data.runStatus).toBe("failed")
    expect(stamped[1].data._runOverlay).toMatchObject({ runError: "boom" })
    const cleared = withoutRunOverlay(stamped)
    expect(cleared[0]).toBe(start)
    expect(cleared[1].data).toEqual(llm.data)
  })

  it("keeps a node's identity during a live run while nothing changed for it", () => {
    const results = { llm_1: result("running") }
    const first = withLiveRunStatus([start, llm], results)
    expect(first[0]).toBe(start)
    expect(first[1].data.runStatus).toBe("running")
    const second = withLiveRunStatus(first, results)
    expect(second[1]).toBe(first[1])
    const done = withLiveRunStatus(first, { llm_1: result("completed", { duration_ms: 40 }) })
    expect(done[1]).not.toBe(first[1])
    expect(done[1].data.runStatus).toBe("completed")
  })

  it("animates an edge only while its source is done and its target is running", () => {
    const edges = [edge("start_1", "llm_1"), edge("llm_1", "end_1")]
    const running = withRunAnimation(edges, {
      start_1: result("completed"),
      llm_1: result("running"),
    })
    expect(running[0].animated).toBe(true)
    expect(running[1]).toBe(edges[1])
    const after = withRunAnimation(running, {
      start_1: result("completed"),
      llm_1: result("completed"),
      end_1: result("retrying"),
    })
    expect(after[0].animated).toBe(false)
    expect(after[1].animated).toBe(true)
  })
})

describe("validation badges", () => {
  const validation = {
    valid: false,
    error: "Workflow is not valid",
    warnings: [
      { node_id: "llm_1", code: "empty_prompt", message: "Prompt is empty" },
      { node_id: null, code: "global", message: "not tied to a step" },
    ],
    field_errors: [
      { loc: "nodes.1.data.temperature", node_id: "llm_1", message: "must be at most 2" },
      { loc: "edges.0", node_id: null, message: "ignored" },
    ],
  }

  it("groups warnings and field errors by node and drops those without one", () => {
    const byNode = validationByNode(validation)
    expect(Object.keys(byNode)).toEqual(["llm_1"])
    expect(byNode.llm_1.warnings).toEqual(["Prompt is empty"])
    expect(byNode.llm_1.errors).toEqual(["temperature: must be at most 2"])
  })

  it("sets badges, clears the ones that no longer apply, and can clear all", () => {
    const withBadge = withValidationState([start, llm], validationByNode(validation))
    expect(withBadge[0]).toBe(start)
    expect(withBadge[1].data._validationState).toBeDefined()
    const moved = withValidationState(withBadge, { start_1: { errors: ["e"], warnings: [] } })
    expect(moved[0].data._validationState).toEqual({ errors: ["e"], warnings: [] })
    expect(moved[1].data).toEqual(llm.data)
    const none = withoutValidationState(moved)
    expect(none.map((n) => n.data)).toEqual([start.data, llm.data])
  })
})

describe("nodeTypesOnCanvas", () => {
  it("lists the node types present", () => {
    expect([...nodeTypesOnCanvas([start, llm, llm])].sort()).toEqual(["llm", "start"])
  })
})
