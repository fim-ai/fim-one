import type { Node } from "@xyflow/react"
import type { NodeDataByType, WorkflowNodeV2Type } from "@/types/workflow-blueprint.generated"

/**
 * What every node settings form receives. `K` is the node type; the shape
 * of `data` and the fields a form may set come from the blueprint v2 types
 * generated from the backend models, so a field renamed or removed there
 * stops this directory from compiling until the form follows.
 */
export interface NodeFormProps<K extends WorkflowNodeV2Type> {
  /** The step's id, for a form that keeps something per step across mounts. */
  nodeId?: string
  data: NodeDataByType[K]
  /** Set one field of the node's data. */
  setField: <F extends keyof NodeDataByType[K]>(field: F, value: NodeDataByType[K][F]) => void
  /** Set several fields in one edit (one change on the canvas, one undo step). */
  setFields: (patch: Partial<NodeDataByType[K]>) => void
  /** Every other node on the canvas: where variable references can come from. */
  otherNodes: Node[]
}
