import { useCallback } from "react"
import type { Dispatch, DragEvent, RefObject, SetStateAction } from "react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"
import { addEdge } from "@xyflow/react"
import type { Connection, Edge, Node, ReactFlowInstance } from "@xyflow/react"

import { defaultNodeData } from "@/lib/workflow-blueprint"
import type { WorkflowNodeType } from "@/types/workflow"
import { edgeId, isConnectionAllowed } from "./canvas-model"

/** MIME type the palette puts the node type under while dragging. */
export const PALETTE_DRAG_TYPE = "application/reactflow-node-type"

interface CanvasConnectionsOptions {
  nodes: Node[]
  edges: Edge[]
  setNodes: Dispatch<SetStateAction<Node[]>>
  setEdges: Dispatch<SetStateAction<Edge[]>>
  rfInstanceRef: RefObject<ReactFlowInstance | null>
  /** Called with the type of each node added from the palette. */
  onNodeAdded: (nodeType: WorkflowNodeType) => void
}

/** Adding to the graph: nodes dropped from the palette and edges drawn between handles. */
export function useCanvasConnections({
  nodes,
  edges,
  setNodes,
  setEdges,
  rfInstanceRef,
  onNodeAdded,
}: CanvasConnectionsOptions) {
  const t = useTranslations("workflows")

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            id: edgeId(params.source, params.sourceHandle, params.target, params.targetHandle),
          },
          eds,
        ),
      )
    },
    [setEdges],
  )

  // Connection validation: don't connect to Start, don't connect from End
  const isValidConnection = useCallback(
    (connection: Connection | { source: string; target: string }) =>
      isConnectionAllowed(nodes, edges, connection),
    [nodes, edges],
  )

  // Drop handler for palette drag-and-drop
  const onDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault()
      const nodeType = event.dataTransfer.getData(PALETTE_DRAG_TYPE) as WorkflowNodeType
      if (!nodeType) return

      // Single Start/End validation
      if (nodeType === "start" && nodes.some((n) => n.type === "start")) {
        toast.error(t("errorDuplicateStart"))
        return
      }
      if (nodeType === "end" && nodes.some((n) => n.type === "end")) {
        toast.error(t("errorDuplicateEnd"))
        return
      }

      // Convert screen coordinates to flow coordinates (zoom-aware)
      const rfInstance = rfInstanceRef.current
      if (!rfInstance) return

      const position = rfInstance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      })
      // Offset to center the node at the drop point (node width ~220px, height ~60px)
      position.x -= 110
      position.y -= 30

      const newNode: Node = {
        id: `${nodeType}_${Date.now()}`,
        type: nodeType,
        position,
        data: defaultNodeData(nodeType),
      }

      setNodes((nds) => [...nds, newNode])
      onNodeAdded(nodeType)
    },
    [setNodes, nodes, t, onNodeAdded, rfInstanceRef],
  )

  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
  }, [])

  return { onConnect, isValidConnection, onDrop, onDragOver }
}
