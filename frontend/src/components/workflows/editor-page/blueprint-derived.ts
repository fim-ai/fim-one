/**
 * What the editor page reads off a blueprint: the schemas saved next to it
 * and the lookups the run panels use.
 */
import type {
  StartNodeData,
  WorkflowBlueprint,
  WorkflowNodeType,
} from "@/types/workflow"

/** A fresh empty blueprint (never share one object between workflows). */
export function emptyBlueprint(): WorkflowBlueprint {
  return { nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } }
}

/** The Start node's input variables. */
export function startVariablesOf(blueprint: WorkflowBlueprint): NonNullable<StartNodeData["variables"]> {
  const startNode = blueprint.nodes.find((n) => n.type === "start")
  const startData = startNode?.data as StartNodeData | undefined
  return startData?.variables ?? []
}

/** `input_schema` saved with the workflow: one entry per Start variable, or null. */
export function inputSchemaOf(blueprint: WorkflowBlueprint): Record<string, unknown> | null {
  const startNode = blueprint.nodes.find((n) => n.type === "start")
  const startData = startNode?.data as StartNodeData | undefined
  const inputSchema: Record<string, unknown> = {}
  if (startData?.variables) {
    for (const v of startData.variables) {
      inputSchema[v.name] = { type: v.type, required: v.required ?? false }
    }
  }
  return Object.keys(inputSchema).length > 0 ? inputSchema : null
}

/** `output_schema` saved with the workflow: one string entry per End mapping key, or null. */
export function outputSchemaOf(blueprint: WorkflowBlueprint): Record<string, unknown> | null {
  const endNode = blueprint.nodes.find((n) => n.type === "end")
  const endData = endNode?.data as { output_mapping?: Record<string, string> } | undefined
  return endData?.output_mapping
    ? Object.fromEntries(Object.keys(endData.output_mapping).map((k) => [k, { type: "string" }]))
    : null
}

/** nodeId -> node type, for display labels. */
export function nodeTypeMapOf(blueprint: WorkflowBlueprint): Record<string, WorkflowNodeType> {
  const map: Record<string, WorkflowNodeType> = {}
  for (const n of blueprint.nodes) {
    map[n.id] = n.type
  }
  return map
}

/** The label a user gave a node, if any (used to name a step in an error). */
export function nodeLabelOf(blueprint: WorkflowBlueprint, nodeId: string): string | undefined {
  const node = blueprint.nodes.find((n) => n.id === nodeId)
  const label = (node?.data as { label?: unknown } | undefined)?.label
  return typeof label === "string" && label ? label : undefined
}

/** File name for an exported workflow. */
export function exportFileName(name: string | null | undefined): string {
  const slug = (name || "workflow")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
  return `${slug}.json`
}

/**
 * The blueprint inside an imported file: the export envelope
 * `{ workflow: { blueprint } }` or the legacy `{ blueprint }`.
 */
export function blueprintOfImport(data: unknown): WorkflowBlueprint | undefined {
  const file = data as { workflow?: { blueprint?: WorkflowBlueprint }; blueprint?: WorkflowBlueprint }
  return file?.workflow?.blueprint ?? file?.blueprint
}
