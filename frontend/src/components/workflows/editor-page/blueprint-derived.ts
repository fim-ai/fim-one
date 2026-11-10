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

/**
 * Whether the Start step has "check inputs before a run starts" on: the
 * server then checks a run's inputs against the Start variables and
 * converts them. Off for a workflow saved before the switch existed.
 */
export function startChecksInputs(blueprint: WorkflowBlueprint): boolean {
  const startNode = blueprint.nodes.find((n) => n.type === "start")
  return (startNode?.data as StartNodeData | undefined)?.validate_inputs === true
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

/** Whether two node type maps list the same nodes with the same types. */
export function sameNodeTypes(
  a: Record<string, WorkflowNodeType>,
  b: Record<string, WorkflowNodeType>,
): boolean {
  const keys = Object.keys(a)
  return keys.length === Object.keys(b).length && keys.every((k) => a[k] === b[k])
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

/**
 * `canvas` with the workflow variables of `current`.
 *
 * The canvas serializes nodes, edges and the viewport only. Workflow
 * variables live in the same blueprint but are edited in their own panel,
 * so a blueprint coming from the canvas takes them from the one in use.
 * `variables` goes last, where the server puts it, so an unchanged
 * blueprint still compares equal to the stored one.
 */
export function withVariablesOf(
  canvas: WorkflowBlueprint,
  current: WorkflowBlueprint,
): WorkflowBlueprint {
  if (current.variables === undefined) return canvas
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { variables: _own, ...rest } = canvas
  return { ...rest, variables: current.variables }
}
