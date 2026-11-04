import { describe, expect, it } from "vitest"

import {
  DEFAULT_NODE_DATA,
  defaultNodeData,
  isWorkflowNodeType,
  serializeBlueprint,
  type CanvasEdge,
  type CanvasNode,
} from "@/lib/workflow-blueprint"
import { WORKFLOW_NODE_V2_TYPES } from "@/types/workflow-blueprint.generated"

// A canvas holding one node of every type, the way React Flow hands them over:
// default data plus the run-time overlay fields the editor injects.
function canvasWithEveryNodeType(): { nodes: CanvasNode[]; edges: CanvasEdge[] } {
  const nodes: CanvasNode[] = WORKFLOW_NODE_V2_TYPES.map((type, i) => ({
    id: `${type}_1`,
    type,
    position: { x: i * 200, y: 0 },
    data: {
      ...defaultNodeData(type),
      label: `${type} step`,
      note: "editor note",
      runStatus: "completed",
      _runOverlay: { durationMs: 12, inputPreview: null, outputPreview: null, runError: null },
      _validationState: { errors: [], warnings: [] },
    },
  }))
  const edges: CanvasEdge[] = nodes.slice(1).map((n, i) => ({
    id: `e${i}`,
    source: nodes[i].id,
    target: n.id,
    sourceHandle: nodes[i].type === "conditionBranch" ? "source-default" : null,
    targetHandle: undefined,
  }))
  return { nodes, edges }
}

describe("serializeBlueprint", () => {
  it("produces a v2 blueprint (snapshot)", () => {
    const { nodes, edges } = canvasWithEveryNodeType()
    const bp = serializeBlueprint(nodes, edges, { x: 10, y: 20, zoom: 1.5 })
    expect(bp).toMatchSnapshot()
  })

  it("strips run-time overlay fields and keeps editor fields", () => {
    const { nodes, edges } = canvasWithEveryNodeType()
    const bp = serializeBlueprint(nodes, edges, { x: 0, y: 0, zoom: 1 })
    expect(bp.schema_version).toBe(2)
    for (const node of bp.nodes ?? []) {
      const data = (node.data ?? {}) as Record<string, unknown>
      expect(data).not.toHaveProperty("runStatus")
      expect(data).not.toHaveProperty("_runOverlay")
      expect(data).not.toHaveProperty("_validationState")
      expect(data.note).toBe("editor note")
    }
  })

  it("omits empty edge handles", () => {
    const bp = serializeBlueprint(
      [],
      [{ id: "e", source: "a", target: "b", sourceHandle: null, targetHandle: undefined }],
      { x: 0, y: 0, zoom: 1 },
    )
    expect(bp.edges).toEqual([{ id: "e", source: "a", target: "b" }])
  })
})

describe("node defaults", () => {
  it("covers every node type with v2 field names", () => {
    expect(Object.keys(DEFAULT_NODE_DATA).sort()).toEqual([...WORKFLOW_NODE_V2_TYPES].sort())
    expect(DEFAULT_NODE_DATA.knowledgeRetrieval).toHaveProperty("kb_ids")
    expect(DEFAULT_NODE_DATA.connector).toHaveProperty("action_id")
  })

  it("returns independent copies", () => {
    const a = defaultNodeData("knowledgeRetrieval")
    ;(a.kb_ids as string[]).push("kb-1")
    expect(DEFAULT_NODE_DATA.knowledgeRetrieval.kb_ids).toEqual([])
  })

  it("recognises v2 node types only", () => {
    expect(isWorkflowNodeType("conditionBranch")).toBe(true)
    expect(isWorkflowNodeType("CONDITION_BRANCH")).toBe(false)
    expect(isWorkflowNodeType("custom")).toBe(false)
  })
})
