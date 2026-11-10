import { useCallback, useRef } from "react"
import type { Dispatch, SetStateAction } from "react"
import { toast } from "sonner"
import { useTranslations } from "next-intl"
import type { Edge, Node } from "@xyflow/react"

import { copySelection, pasteClipboard, type CanvasClipboard } from "./canvas-model"

interface CanvasEditingOptions {
  nodes: Node[]
  edges: Edge[]
  setNodes: Dispatch<SetStateAction<Node[]>>
  setEdges: Dispatch<SetStateAction<Edge[]>>
  /** The node whose settings panel is open, if any. */
  selectedNodeId: string | null
  setSelectedNodeId: (id: string | null) => void
}

/**
 * Editing operations on the selection: delete, copy, paste, duplicate and
 * select all. Start and End cannot be deleted or pasted.
 */
export function useCanvasEditing({
  nodes,
  edges,
  setNodes,
  setEdges,
  selectedNodeId,
  setSelectedNodeId,
}: CanvasEditingOptions) {
  const t = useTranslations("workflows")

  // Clipboard for copy/paste (stored in ref to avoid re-renders)
  const clipboardRef = useRef<CanvasClipboard>({ nodes: [], edges: [] })

  // Delete selected nodes/edges handler
  const deleteSelected = useCallback(() => {
    // Get currently selected nodes
    const selectedNodes = nodes.filter((n) => n.selected)
    // Get currently selected edges
    const selectedEdges = edges.filter((e) => e.selected)

    if (selectedNodes.length === 0 && selectedEdges.length === 0) return

    // Check for protected node types (start, end)
    for (const node of selectedNodes) {
      if (node.type === "start") {
        toast.error(t("errorCannotDeleteStart"))
        return
      }
      if (node.type === "end") {
        toast.error(t("errorCannotDeleteEnd"))
        return
      }
    }

    const deletedNodeIds = new Set(selectedNodes.map((n) => n.id))
    const deletedEdgeIds = new Set(selectedEdges.map((e) => e.id))

    // Remove selected nodes
    if (deletedNodeIds.size > 0) {
      setNodes((nds) => nds.filter((n) => !deletedNodeIds.has(n.id)))
      // Remove edges connected to deleted nodes
      setEdges((eds) =>
        eds.filter(
          (e) =>
            !deletedNodeIds.has(e.source) &&
            !deletedNodeIds.has(e.target) &&
            !deletedEdgeIds.has(e.id),
        ),
      )
    } else if (deletedEdgeIds.size > 0) {
      // Only edges selected, no nodes
      setEdges((eds) => eds.filter((e) => !deletedEdgeIds.has(e.id)))
    }

    // Clear selection if deleted node was the config-panel selected node
    if (selectedNodeId && deletedNodeIds.has(selectedNodeId)) {
      setSelectedNodeId(null)
    }
  }, [nodes, edges, selectedNodeId, setNodes, setEdges, setSelectedNodeId, t])

  // Delete a specific node by ID (used by config panel)
  const deleteNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId)
      if (!node) return

      if (node.type === "start") {
        toast.error(t("errorCannotDeleteStart"))
        return
      }
      if (node.type === "end") {
        toast.error(t("errorCannotDeleteEnd"))
        return
      }

      setNodes((nds) => nds.filter((n) => n.id !== nodeId))
      setEdges((eds) =>
        eds.filter((e) => e.source !== nodeId && e.target !== nodeId),
      )

      if (selectedNodeId === nodeId) {
        setSelectedNodeId(null)
      }
    },
    [nodes, selectedNodeId, setNodes, setEdges, setSelectedNodeId, t],
  )

  // --- Copy selected nodes into the clipboard ref ---
  const copySelected = useCallback(() => {
    const copied = copySelection(nodes, edges)
    if (copied) clipboardRef.current = copied
  }, [nodes, edges])

  /** Put one node on the clipboard, selected or not (context menu "Copy"). */
  const copyNode = useCallback((node: Node) => {
    clipboardRef.current = {
      nodes: [{ ...node, data: { ...node.data } }],
      edges: [],
    }
  }, [])

  // --- Paste copied nodes with offset and new IDs ---
  const paste = useCallback(() => {
    if (clipboardRef.current.nodes.length === 0) return
    const { nodes: newNodes, edges: newEdges } = pasteClipboard(clipboardRef.current, Date.now())
    if (newNodes.length === 0) return

    // Deselect existing nodes, add pasted nodes as selected
    setNodes((nds) => [
      ...nds.map((n) => (n.selected ? { ...n, selected: false } : n)),
      ...newNodes,
    ])
    setEdges((eds) => [...eds, ...newEdges])
  }, [setNodes, setEdges])

  // --- Duplicate = copy + paste in one step ---
  const duplicateSelected = useCallback(() => {
    copySelected()
    paste()
  }, [copySelected, paste])

  /**
   * Duplicate from a node's context menu: the node that was right-clicked.
   * When that node is part of the selection the whole selection is
   * duplicated, as Cmd+D does; otherwise only that node is, whatever else
   * is selected, and the clipboard is left as it was.
   */
  const duplicateNode = useCallback(
    (nodeId: string) => {
      const node = nodes.find((n) => n.id === nodeId)
      if (!node) return
      if (node.selected) {
        duplicateSelected()
        return
      }
      const { nodes: newNodes } = pasteClipboard(
        { nodes: [{ ...node, data: { ...node.data } }], edges: [] },
        Date.now(),
      )
      if (newNodes.length === 0) return
      setNodes((nds) => [
        ...nds.map((n) => (n.selected ? { ...n, selected: false } : n)),
        ...newNodes,
      ])
    },
    [nodes, duplicateSelected, setNodes],
  )

  // --- Select all nodes ---
  const selectAll = useCallback(() => {
    setNodes((nds) => nds.map((n) => (n.selected ? n : { ...n, selected: true })))
  }, [setNodes])

  /** Whether there is something to paste. */
  const hasClipboard = useCallback(() => clipboardRef.current.nodes.length > 0, [])

  return {
    deleteSelected,
    deleteNode,
    copySelected,
    copyNode,
    paste,
    duplicateSelected,
    duplicateNode,
    selectAll,
    hasClipboard,
  }
}
