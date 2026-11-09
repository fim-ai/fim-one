import { describe, expect, it } from "vitest"
import type { Edge, Node } from "@xyflow/react"

import { blueprintEdges, blueprintNodes } from "@/components/workflows/editor/canvas-model"
import { withVariablesOf } from "@/components/workflows/editor-page/blueprint-derived"
import { defaultNodeData, serializeBlueprint, type CanvasNode } from "@/lib/workflow-blueprint"
import { WORKFLOW_NODE_V2_TYPES } from "@/types/workflow-blueprint.generated"
import type { WorkflowBlueprint } from "@/types/workflow"

/**
 * Graph <-> blueprint v2 round trip.
 *
 * The editor decides "unsaved" by comparing the JSON text of what the
 * canvas serializes with the JSON text of what the server returned, so the
 * round trip has to reproduce a stored blueprint exactly, key order
 * included: otherwise merely opening a workflow marks it unsaved and the
 * automatic save rewrites it.
 */

/** A stored blueprint with one node of every type, in the key order the server uses. */
function storedBlueprint(): WorkflowBlueprint {
  const nodes: WorkflowBlueprint["nodes"] = WORKFLOW_NODE_V2_TYPES.map((type, i) => ({
    id: `${type}_1`,
    type,
    position: { x: i * 240, y: 96 },
    data: {
      ...defaultNodeData(type),
      note: `${type} note`,
      comment: "kept",
      error_strategy: "continue",
      retry_count: 1,
    },
  }))
  // A branch with two conditions, so the edges carry different source handles.
  const branch = nodes.find((n) => n.type === "conditionBranch")!
  branch.data = {
    ...branch.data,
    conditions: [
      { id: "c1", label: "yes", variable: "{{llm_1.llm_result}}", operator: "contains", value: "yes" },
      { id: "c2", label: "no", variable: "{{llm_1.llm_result}}", operator: "is_empty" },
    ],
  }
  const edges = [
    { id: "e1", source: "start_1", target: "llm_1", sourceHandle: "source", targetHandle: "target" },
    { id: "e2", source: "llm_1", target: "conditionBranch_1", sourceHandle: "source", targetHandle: "target" },
    { id: "e3", source: "conditionBranch_1", target: "agent_1", sourceHandle: "condition-c1", targetHandle: "target" },
    { id: "e4", source: "conditionBranch_1", target: "mcp_1", sourceHandle: "condition-c2", targetHandle: "target" },
    { id: "e5", source: "agent_1", target: "end_1", sourceHandle: "source", targetHandle: "target" },
  ]
  return { schema_version: 2, nodes, edges, viewport: { x: 12, y: -8, zoom: 0.75 } }
}

function roundTrip(blueprint: WorkflowBlueprint, mutate?: (nodes: Node[], edges: Edge[]) => void) {
  const nodes = blueprintNodes(blueprint)
  const edges = blueprintEdges(blueprint)
  mutate?.(nodes, edges)
  return serializeBlueprint(nodes as CanvasNode[], edges, blueprint.viewport)
}

describe("blueprint round trip", () => {
  it("blueprint -> canvas -> blueprint is the same blueprint", () => {
    const stored = storedBlueprint()
    expect(roundTrip(stored)).toEqual(stored)
  })

  it("reproduces the stored JSON text, so opening a workflow is not an edit", () => {
    const stored = storedBlueprint()
    expect(JSON.stringify(roundTrip(stored))).toBe(JSON.stringify(stored))
  })

  it("is not disturbed by what React Flow and the editor add to nodes and edges at run time", () => {
    const stored = storedBlueprint()
    const out = roundTrip(stored, (nodes, edges) => {
      for (const node of nodes) {
        node.selected = true
        node.dragging = false
        node.measured = { width: 220, height: 60 }
        node.className = "!opacity-100"
        node.style = { opacity: 0.25 }
        node.data = {
          ...node.data,
          runStatus: "completed",
          _runOverlay: { durationMs: 3, inputPreview: null, outputPreview: "x", runError: null },
          _validationState: { errors: ["e"], warnings: [] },
        }
      }
      for (const edge of edges) {
        edge.selected = true
        edge.animated = true
        edge.type = "default"
      }
    })
    expect(JSON.stringify(out)).toBe(JSON.stringify(stored))
  })

  it("canvas -> blueprint -> canvas keeps ids, types, positions, settings and handles", () => {
    const stored = storedBlueprint()
    const again = blueprintNodes(roundTrip(stored) as unknown as WorkflowBlueprint)
    expect(again).toEqual(blueprintNodes(stored))
    const edgesAgain = blueprintEdges(roundTrip(stored) as unknown as WorkflowBlueprint)
    expect(edgesAgain).toEqual(blueprintEdges(stored))
  })

  it("an edge stored without handles comes back without handles", () => {
    const stored: WorkflowBlueprint = {
      schema_version: 2,
      nodes: [],
      edges: [{ id: "e", source: "a", target: "b" }],
      viewport: { x: 0, y: 0, zoom: 1 },
    }
    expect(JSON.stringify(roundTrip(stored))).toBe(JSON.stringify(stored))
  })

  it("an edit shows up as a difference", () => {
    const stored = storedBlueprint()
    const moved = roundTrip(stored, (nodes) => {
      nodes[0].position = { x: 1, y: 2 }
    })
    expect(JSON.stringify(moved)).not.toBe(JSON.stringify(stored))
    const edited = roundTrip(stored, (nodes) => {
      nodes[2].data = { ...nodes[2].data, prompt_template: "changed" }
    })
    expect(edited.nodes?.[2].data).toMatchObject({ prompt_template: "changed" })
    const disconnected = roundTrip(stored, (_nodes, edges) => {
      edges.pop()
    })
    expect(disconnected.edges).toHaveLength(4)
  })

  it("workflow variables ride along, last, as the server stores them", () => {
    const variables = [{ name: "region", type: "string" as const, default_value: "eu", description: "" }]
    const stored = { ...storedBlueprint(), variables }
    const canvas = roundTrip(stored) as unknown as WorkflowBlueprint
    expect(canvas).not.toHaveProperty("variables")
    expect(JSON.stringify(withVariablesOf(canvas, stored))).toBe(JSON.stringify(stored))
    // A blueprint without variables is handed through untouched.
    const plain = storedBlueprint()
    expect(withVariablesOf(plain, plain)).toBe(plain)
  })
})
