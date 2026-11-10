/**
 * Canvas ↔ blueprint v2.
 *
 * The backend validates every saved blueprint against the v2 models in
 * `src/fim_one/core/workflow/blueprint_v2.py`; the types used here are
 * generated from those models. Renaming or removing a field there without
 * updating this file (and the node components) fails `tsc`.
 */
import type {
  BlueprintV2,
  NodeDataByType,
  WorkflowEdgeV2,
  WorkflowNodeV2,
  WorkflowNodeV2Type,
} from "@/types/workflow-blueprint.generated"
import {
  BLUEPRINT_SCHEMA_VERSION,
  WORKFLOW_NODE_V2_TYPES,
} from "@/types/workflow-blueprint.generated"

/** Data a freshly added node starts with, per node type. */
export const DEFAULT_NODE_DATA: { [K in WorkflowNodeV2Type]: NodeDataByType[K] } = {
  // New Start steps have the server check a run's inputs; a Start step
  // saved without the flag stays unchecked until its author switches it on.
  start: { variables: [], validate_inputs: true },
  end: { output_mapping: {} },
  llm: { prompt_template: "", output_variable: "llm_result", temperature: 0.7 },
  conditionBranch: { mode: "expression", conditions: [] },
  agent: { agent_id: "", prompt_template: "", output_variable: "agent_result" },
  knowledgeRetrieval: { kb_ids: [], query_template: "", top_k: 5, output_variable: "kb_result" },
  connector: { connector_id: "", action_id: "", parameters: {}, output_variable: "connector_result" },
  humanIntervention: {
    prompt_message: "",
    assignee: "",
    timeout_hours: 24,
    output_variable: "approval_result",
  },
  mcp: { server_id: "", tool_name: "", parameters: {}, output_variable: "mcp_result" },
}

/** Fresh, mutable copy of a node type's default data. */
export function defaultNodeData(type: WorkflowNodeV2Type): Record<string, unknown> {
  return structuredClone(DEFAULT_NODE_DATA[type]) as Record<string, unknown>
}

/** The blueprint a new blank workflow starts with: Start and End, not yet connected. */
export function blankWorkflowBlueprint(): {
  nodes: Array<{
    id: string
    type: WorkflowNodeV2Type
    position: { x: number; y: number }
    data: Record<string, unknown>
  }>
  edges: never[]
  viewport: CanvasViewport
} {
  return {
    nodes: [
      { id: "start_1", type: "start", position: { x: 100, y: 200 }, data: defaultNodeData("start") },
      { id: "end_1", type: "end", position: { x: 600, y: 200 }, data: defaultNodeData("end") },
    ],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
  }
}

/** Keys the canvas injects into node data at run time; never persisted. */
const TRANSIENT_KEYS = new Set(["runStatus", "_runOverlay", "_validationState"])

export function isWorkflowNodeType(value: unknown): value is WorkflowNodeV2Type {
  return typeof value === "string" && (WORKFLOW_NODE_V2_TYPES as readonly string[]).includes(value)
}

export interface CanvasNode {
  id: string
  type?: string
  position: { x: number; y: number }
  data: Record<string, unknown>
}

export interface CanvasEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string | null
  targetHandle?: string | null
}

export interface CanvasViewport {
  x: number
  y: number
  zoom: number
}

/**
 * Serialize the canvas into a v2 blueprint.
 *
 * Node data keeps editor-only keys (note, comment, label ...) and drops the
 * run-time overlay. A node whose type is not a v2 type is passed through
 * unchanged so the backend rejects it with a field-level error.
 */
export function serializeBlueprint(
  nodes: CanvasNode[],
  edges: CanvasEdge[],
  viewport: CanvasViewport,
): BlueprintV2 {
  return {
    schema_version: BLUEPRINT_SCHEMA_VERSION,
    nodes: nodes.map((n) => {
      const data: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(n.data ?? {})) {
        if (!TRANSIENT_KEYS.has(key)) data[key] = value
      }
      return {
        id: n.id,
        type: n.type,
        position: { x: n.position.x, y: n.position.y },
        data,
      } as WorkflowNodeV2
    }),
    edges: edges.map((e) => {
      const edge: WorkflowEdgeV2 = {
        id: e.id,
        source: e.source,
        target: e.target,
      }
      if (e.sourceHandle) edge.sourceHandle = e.sourceHandle
      if (e.targetHandle) edge.targetHandle = e.targetHandle
      return edge
    }),
    viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom },
  }
}

/**
 * The graph as text: what a save would store of these nodes and edges,
 * without the viewport. Two canvas states with the same signature differ
 * only in things that are not part of the workflow (which node is selected
 * or being dragged, measured sizes, run and validation overlays).
 */
export function graphSignature(nodes: CanvasNode[], edges: CanvasEdge[]): string {
  const { nodes: n, edges: e } = serializeBlueprint(nodes, edges, { x: 0, y: 0, zoom: 1 })
  return JSON.stringify({ nodes: n, edges: e })
}
