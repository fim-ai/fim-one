"use client"

import { useCallback, useRef, useState, useEffect, useMemo, useImperativeHandle, forwardRef } from "react"
import { useTheme } from "next-themes"
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  BackgroundVariant,
} from "@xyflow/react"
import type {
  Connection,
  Edge,
  NodeMouseHandler,
  Node,
  EdgeTypes,
  ReactFlowInstance,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { toast } from "sonner"
import { useTranslations } from "next-intl"
import { Copy, Trash2, Settings, Clipboard, MousePointerSquareDashed, Maximize2, LayoutGrid } from "lucide-react"

import { useWorkflowHistory } from "@/hooks/use-workflow-history"
import { getAutoLayoutedNodes } from "./auto-layout"
import { NodePalette } from "./node-palette"
import { NodeConfigPanel } from "./node-config-panel"
import { RunPanel } from "./run-panel"
import { AddNodeEdge } from "./add-node-edge"
import { KeyboardShortcutsDialog } from "./keyboard-shortcuts-dialog"
import type {
  WorkflowBlueprint,
  WorkflowNodeType,
  StartNodeData,
  NodeRunResult,
} from "@/types/workflow"

import { StartNode } from "./nodes/start-node"
import { EndNode } from "./nodes/end-node"
import { LLMNode } from "./nodes/llm-node"
import { ConditionBranchNode } from "./nodes/condition-branch-node"
import { QuestionClassifierNode } from "./nodes/question-classifier-node"
import { AgentNode } from "./nodes/agent-node"
import { KnowledgeRetrievalNode } from "./nodes/knowledge-retrieval-node"
import { ConnectorNode } from "./nodes/connector-node"
import { HTTPRequestNode } from "./nodes/http-request-node"
import { VariableAssignNode } from "./nodes/variable-assign-node"
import { TemplateTransformNode } from "./nodes/template-transform-node"
import { CodeExecutionNode } from "./nodes/code-execution-node"
import { IteratorNode } from "./nodes/iterator-node"
import { LoopNode } from "./nodes/loop-node"
import { VariableAggregatorNode } from "./nodes/variable-aggregator-node"
import { ParameterExtractorNode } from "./nodes/parameter-extractor-node"
import { ListOperationNode } from "./nodes/list-operation-node"
import { TransformNode } from "./nodes/transform-node"
import { DocumentExtractorNode } from "./nodes/document-extractor-node"
import { QuestionUnderstandingNode } from "./nodes/question-understanding-node"
import { HumanInterventionNode } from "./nodes/human-intervention-node"
import { MCPNode } from "./nodes/mcp-node"
import { BuiltinToolNode } from "./nodes/builtin-tool-node"
import { SubWorkflowNode } from "./nodes/sub-workflow-node"
import { ENVNode } from "./nodes/env-node"

// MUST be defined outside the component to prevent ReactFlow infinite re-renders
const nodeTypes = {
  start: StartNode,
  end: EndNode,
  llm: LLMNode,
  conditionBranch: ConditionBranchNode,
  questionClassifier: QuestionClassifierNode,
  agent: AgentNode,
  knowledgeRetrieval: KnowledgeRetrievalNode,
  connector: ConnectorNode,
  httpRequest: HTTPRequestNode,
  variableAssign: VariableAssignNode,
  templateTransform: TemplateTransformNode,
  codeExecution: CodeExecutionNode,
  iterator: IteratorNode,
  loop: LoopNode,
  variableAggregator: VariableAggregatorNode,
  parameterExtractor: ParameterExtractorNode,
  listOperation: ListOperationNode,
  transform: TransformNode,
  documentExtractor: DocumentExtractorNode,
  questionUnderstanding: QuestionUnderstandingNode,
  humanIntervention: HumanInterventionNode,
  mcp: MCPNode,
  builtinTool: BuiltinToolNode,
  subWorkflow: SubWorkflowNode,
  env: ENVNode,
}

// Custom edge types - defined outside component for stability
const edgeTypes: EdgeTypes = {
  default: AddNodeEdge,
}

// Minimap node colors — mirrors categoryColorMap in base-workflow-node.tsx
const minimapNodeColor: Record<string, string> = {
  start: "#22c55e",
  end: "#ef4444",
  llm: "#3b82f6",
  questionClassifier: "#14b8a6",
  agent: "#6366f1",
  knowledgeRetrieval: "#14b8a6",
  conditionBranch: "#f97316",
  connector: "#a855f7",
  httpRequest: "#64748b",
  variableAssign: "#6b7280",
  templateTransform: "#f59e0b",
  codeExecution: "#10b981",
  iterator: "#06b6d4",
  loop: "#f97316",
  variableAggregator: "#0ea5e9",
  parameterExtractor: "#8b5cf6",
  listOperation: "#84cc16",
  transform: "#f43f5e",
  documentExtractor: "#d97706",
  questionUnderstanding: "#ec4899",
  humanIntervention: "#0ea5e9",
  mcp: "#8b5cf6",
  builtinTool: "#71717a",
  subWorkflow: "#6366f1",
  env: "#d97706",
}

const getMinimapNodeColor = (node: Node) => minimapNodeColor[node.type ?? ""] ?? "#6b7280"

const defaultNodeData: Record<WorkflowNodeType, Record<string, unknown>> = {
  start: { variables: [] },
  end: { output_mapping: {} },
  llm: { prompt_template: "", output_variable: "llm_result", temperature: 0.7 },
  conditionBranch: { mode: "expression", conditions: [] },
  questionClassifier: { classes: [] },
  agent: { agent_id: "", output_variable: "agent_result" },
  knowledgeRetrieval: { kb_id: "", query_template: "", top_k: 5, output_variable: "kb_result" },
  connector: { connector_id: "", action: "", parameters: {}, output_variable: "connector_result" },
  httpRequest: { method: "GET", url: "", output_variable: "http_result" },
  variableAssign: { assignments: [] },
  templateTransform: { template: "", output_variable: "template_result" },
  codeExecution: { language: "python", code: "", output_variable: "code_result" },
  iterator: { list_variable: "", iterator_variable: "current_item", index_variable: "current_index", max_iterations: 100 },
  loop: { condition: "", max_iterations: 50, loop_variable: "loop_index" },
  variableAggregator: { variables: [], mode: "list", separator: "\n" },
  parameterExtractor: { input_text: "", parameters: [], extraction_prompt: "" },
  listOperation: { input_variable: "", operation: "filter", expression: "", output_variable: "list_result" },
  transform: { input_variable: "", operations: [], output_variable: "transform_result" },
  documentExtractor: { input_variable: "", input_type: "text", extract_mode: "full_text", output_variable: "document_result" },
  questionUnderstanding: { input_variable: "", mode: "rewrite", output_variable: "question_result" },
  humanIntervention: { prompt_message: "", assignee: "", timeout_hours: 24, output_variable: "approval_result" },
  mcp: { server_id: "", tool_name: "", parameters: {}, output_variable: "mcp_result" },
  builtinTool: { tool_id: "", parameters: {}, output_variable: "tool_result" },
  subWorkflow: { workflow_id: "", input_mapping: {}, output_variable: "sub_result" },
  env: { env_keys: [], output_variable: "env_result" },
}

interface WorkflowEditorProps {
  blueprint: WorkflowBlueprint
  onBlueprintChange: (blueprint: WorkflowBlueprint) => void
  onUndoRedoChange?: (canUndo: boolean, canRedo: boolean) => void
  isRunning: boolean
  runPanelOpen: boolean
  startVariables: StartNodeData["variables"]
  nodeResults: Record<string, NodeRunResult> | null
  finalOutputs: Record<string, unknown> | null
  finalError: string | null
  runDuration: number | null
  nodeTypeMap: Record<string, WorkflowNodeType>
  totalNodeCount: number
  onStartRun: (inputs: Record<string, unknown>) => void
  onRunAgain: () => void
  onCancelRun: () => void
  onCloseRunPanel: () => void
}

export interface WorkflowEditorHandle {
  autoLayout: () => void
  undo: () => void
  redo: () => void
  canUndo: boolean
  canRedo: boolean
}

export const WorkflowEditor = forwardRef<WorkflowEditorHandle, WorkflowEditorProps>(function WorkflowEditor({
  blueprint,
  onBlueprintChange,
  onUndoRedoChange,
  isRunning,
  runPanelOpen,
  startVariables,
  nodeResults,
  finalOutputs,
  finalError,
  runDuration,
  nodeTypeMap,
  totalNodeCount,
  onStartRun,
  onRunAgain,
  onCancelRun,
  onCloseRunPanel,
}, ref) {
  const t = useTranslations("workflows")
  const { resolvedTheme } = useTheme()
  const rfColorMode = resolvedTheme === "dark" ? "dark" : "light"
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null)

  const [nodes, setNodes, onNodesChange] = useNodesState(
    blueprint.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: n.data,
    } as Node)),
  )
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    blueprint.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? undefined,
      targetHandle: e.targetHandle ?? undefined,
    } as Edge)),
  )
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useState<
    | { type: "node"; x: number; y: number; nodeId: string }
    | { type: "pane"; x: number; y: number }
    | null
  >(null)
  const [shortcutsDialogOpen, setShortcutsDialogOpen] = useState(false)

  // --- Undo/Redo history ---
  const initialNodesRef = useRef(
    blueprint.nodes.map((n) => ({
      id: n.id,
      type: n.type,
      position: n.position,
      data: n.data,
    } as Node)),
  )
  const initialEdgesRef = useRef(
    blueprint.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
    })),
  )
  const { pushState, undo, redo, canUndo, canRedo } = useWorkflowHistory(
    initialNodesRef.current,
    initialEdgesRef.current,
  )

  // Track whether we are currently restoring from history to avoid re-pushing
  const isRestoringRef = useRef(false)

  // Clipboard for copy/paste (stored in ref to avoid re-renders)
  const copiedNodesRef = useRef<{ nodes: Node[]; edges: Edge[] }>({ nodes: [], edges: [] })

  // Push nodes/edges to history whenever they change (skipped during restore)
  useEffect(() => {
    if (isRestoringRef.current) {
      isRestoringRef.current = false
      return
    }
    pushState(nodes, edges)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges])

  // Normalize edges: convert null sourceHandle/targetHandle to undefined
  // (React Flow Edge type allows null, but useEdgesState infers stricter types)
  const toSafeEdges = useCallback(
    (raw: Edge[]) =>
      raw.map((e) => ({
        ...e,
        sourceHandle: e.sourceHandle ?? undefined,
        targetHandle: e.targetHandle ?? undefined,
      })),
    [],
  )

  const handleUndo = useCallback(() => {
    const snapshot = undo()
    if (!snapshot) return
    isRestoringRef.current = true
    setNodes(snapshot.nodes)
    setEdges(toSafeEdges(snapshot.edges))
  }, [undo, setNodes, setEdges, toSafeEdges])

  const handleRedo = useCallback(() => {
    const snapshot = redo()
    if (!snapshot) return
    isRestoringRef.current = true
    setNodes(snapshot.nodes)
    setEdges(toSafeEdges(snapshot.edges))
  }, [redo, setNodes, setEdges, toSafeEdges])

  // Delete selected nodes/edges handler
  const handleDeleteSelected = useCallback(() => {
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
  }, [nodes, edges, selectedNodeId, setNodes, setEdges, t])

  // Delete a specific node by ID (used by config panel)
  const handleDeleteNode = useCallback(
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
    [nodes, selectedNodeId, setNodes, setEdges, t],
  )

  // --- Copy selected nodes into the clipboard ref ---
  const handleCopySelected = useCallback(() => {
    const selectedNodes = nodes.filter((n) => n.selected)
    if (selectedNodes.length === 0) return

    const selectedIds = new Set(selectedNodes.map((n) => n.id))
    // Only copy edges that connect two selected nodes (internal edges)
    const internalEdges = edges.filter(
      (e) => selectedIds.has(e.source) && selectedIds.has(e.target),
    )

    copiedNodesRef.current = {
      nodes: selectedNodes.map((n) => ({ ...n, data: { ...n.data } })),
      edges: internalEdges.map((e) => ({ ...e })),
    }
  }, [nodes, edges])

  // --- Paste copied nodes with offset and new IDs ---
  const handlePaste = useCallback(() => {
    const { nodes: copiedNodes, edges: copiedEdges } = copiedNodesRef.current
    if (copiedNodes.length === 0) return

    // Build old-id -> new-id mapping
    const idMap = new Map<string, string>()
    const now = Date.now()
    for (let i = 0; i < copiedNodes.length; i++) {
      const oldId = copiedNodes[i].id
      const nodeType = copiedNodes[i].type ?? "node"
      idMap.set(oldId, `${nodeType}_${now}_${i}`)
    }

    // Create new nodes with offset position; skip start/end (singleton constraint)
    const newNodes: Node[] = []
    for (const cn of copiedNodes) {
      if (cn.type === "start" || cn.type === "end") continue
      newNodes.push({
        ...cn,
        id: idMap.get(cn.id)!,
        position: { x: cn.position.x + 50, y: cn.position.y + 50 },
        data: { ...cn.data },
        selected: true,
      })
    }

    if (newNodes.length === 0) return

    const newNodeIds = new Set(newNodes.map((n) => n.id))

    // Recreate internal edges with new IDs
    const newEdges: Edge[] = []
    for (const ce of copiedEdges) {
      const newSource = idMap.get(ce.source)
      const newTarget = idMap.get(ce.target)
      // Only create edge if both endpoints were pasted (start/end filtering)
      if (newSource && newTarget && newNodeIds.has(newSource) && newNodeIds.has(newTarget)) {
        newEdges.push({
          ...ce,
          id: `e-${newSource}-${ce.sourceHandle ?? "default"}-${newTarget}-${ce.targetHandle ?? "default"}`,
          source: newSource,
          target: newTarget,
        })
      }
    }

    // Deselect existing nodes, add pasted nodes as selected
    setNodes((nds) => [
      ...nds.map((n) => (n.selected ? { ...n, selected: false } : n)),
      ...newNodes,
    ])
    setEdges((eds) => [...eds, ...newEdges])
  }, [setNodes, setEdges])

  // --- Duplicate = copy + paste in one step ---
  const handleDuplicateSelected = useCallback(() => {
    handleCopySelected()
    handlePaste()
  }, [handleCopySelected, handlePaste])

  // --- Select all nodes ---
  const handleSelectAll = useCallback(() => {
    setNodes((nds) => nds.map((n) => (n.selected ? n : { ...n, selected: true })))
  }, [setNodes])

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't intercept when typing in an input/textarea or contentEditable
      const target = e.target as HTMLElement
      const tag = target?.tagName
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return
      if (target?.isContentEditable) return

      const modKey = e.metaKey || e.ctrlKey
      const key = e.key.toLowerCase()

      // Undo: Cmd+Z (without Shift)
      if (modKey && key === "z" && !e.shiftKey) {
        e.preventDefault()
        handleUndo()
        return
      }

      // Redo: Cmd+Shift+Z or Cmd+Y
      if (modKey && ((key === "z" && e.shiftKey) || key === "y")) {
        e.preventDefault()
        handleRedo()
        return
      }

      // Copy: Cmd+C
      if (modKey && key === "c" && !e.shiftKey) {
        e.preventDefault()
        handleCopySelected()
        return
      }

      // Paste: Cmd+V
      if (modKey && key === "v" && !e.shiftKey) {
        e.preventDefault()
        handlePaste()
        return
      }

      // Select all: Cmd+A
      if (modKey && key === "a" && !e.shiftKey) {
        e.preventDefault()
        handleSelectAll()
        return
      }

      // Duplicate: Cmd+D
      if (modKey && key === "d" && !e.shiftKey) {
        e.preventDefault()
        handleDuplicateSelected()
        return
      }

      // Show shortcuts dialog: ?
      if (e.key === "?" && !modKey) {
        e.preventDefault()
        setShortcutsDialogOpen(true)
        return
      }

      // Escape: deselect all nodes and close config panel
      if (e.key === "Escape") {
        e.preventDefault()
        setNodes((nds) => nds.map((n) => (n.selected ? { ...n, selected: false } : n)))
        setSelectedNodeId(null)
        return
      }

      // Delete selected nodes/edges: Backspace or Delete
      if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault()
        handleDeleteSelected()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [handleUndo, handleRedo, handleDeleteSelected, handleCopySelected, handlePaste, handleSelectAll, handleDuplicateSelected, setNodes])

  // Notify parent of undo/redo state changes
  useEffect(() => {
    onUndoRedoChange?.(canUndo, canRedo)
  }, [canUndo, canRedo, onUndoRedoChange])

  // Track which node types exist for palette dimming
  const existingNodeTypes = useMemo(() => {
    const types = new Set<string>()
    for (const n of nodes) {
      if (n.type) types.add(n.type)
    }
    return types
  }, [nodes])

  // Derived nodes with run status + overlay merged — pure derivation, no state mutation
  const displayNodes = useMemo(() => {
    if (!nodeResults || (!runPanelOpen && !isRunning)) return nodes
    return nodes.map((node) => {
      const result = nodeResults[node.id]
      const newStatus = result?.status
      if (!newStatus) return node

      // Build overlay data for duration badge & tooltip
      const truncate = (v: unknown): string | null => {
        if (v == null) return null
        const s = typeof v === "string" ? v : JSON.stringify(v)
        return s.length > 120 ? s.slice(0, 120) + "..." : s
      }
      const _runOverlay = {
        durationMs: result.duration_ms ?? null,
        inputPreview: truncate(result.input_preview),
        outputPreview: truncate(result.output),
        runError: result.error ?? null,
      }

      // Skip update if status and overlay haven't changed
      const prev = node.data._runOverlay as typeof _runOverlay | undefined
      if (
        node.data.runStatus === newStatus &&
        prev?.durationMs === _runOverlay.durationMs &&
        prev?.runError === _runOverlay.runError
      ) {
        return node
      }

      return { ...node, data: { ...node.data, runStatus: newStatus, _runOverlay } }
    })
  }, [nodes, nodeResults, runPanelOpen, isRunning])

  // Derived edges: animate when source is completed and target is running
  const displayEdges = useMemo(() => {
    if (!nodeResults || (!runPanelOpen && !isRunning)) return edges
    return edges.map((edge) => {
      const sourceStatus = nodeResults[edge.source]?.status
      const targetStatus = nodeResults[edge.target]?.status
      const shouldAnimate = sourceStatus === "completed" && (targetStatus === "running" || targetStatus === "retrying")
      if (!shouldAnimate && !edge.animated) return edge
      return { ...edge, animated: shouldAnimate }
    })
  }, [edges, nodeResults, runPanelOpen, isRunning])

  // Sync blueprint out when nodes/edges change (debounced)
  const syncTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (syncTimer.current) clearTimeout(syncTimer.current)
    syncTimer.current = setTimeout(() => {
      onBlueprintChange({
        nodes: nodes.map((n) => ({
          id: n.id,
          type: n.type as WorkflowNodeType,
          position: n.position,
          data: n.data as Record<string, unknown>,
        })),
        edges: edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          sourceHandle: e.sourceHandle ?? undefined,
          targetHandle: e.targetHandle ?? undefined,
        })),
        viewport: blueprint.viewport,
      })
    }, 300)
    return () => {
      if (syncTimer.current) clearTimeout(syncTimer.current)
    }
    // Only sync when nodes/edges change, not blueprint ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, onBlueprintChange])

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            id: `e-${params.source}-${params.sourceHandle ?? "default"}-${params.target}-${params.targetHandle ?? "default"}`,
          },
          eds,
        ),
      )
    },
    [setEdges],
  )

  // Connection validation: don't connect to Start, don't connect from End
  const isValidConnection = useCallback(
    (connection: Connection | { source: string; target: string }) => {
      // No self-connections
      if (connection.source === connection.target) return false

      const targetNode = nodes.find((n) => n.id === connection.target)
      const sourceNode = nodes.find((n) => n.id === connection.source)
      // Can't connect TO a Start node or FROM an End node
      if (targetNode?.type === "start") return false
      if (sourceNode?.type === "end") return false

      // No duplicate edges (same source → target, same handles)
      const srcHandle = (connection as Connection).sourceHandle ?? null
      const tgtHandle = (connection as Connection).targetHandle ?? null
      const duplicate = edges.some(
        (e) =>
          e.source === connection.source &&
          e.target === connection.target &&
          (e.sourceHandle ?? null) === srcHandle &&
          (e.targetHandle ?? null) === tgtHandle,
      )
      if (duplicate) return false

      return true
    },
    [nodes, edges],
  )

  // Drop handler for palette drag-and-drop
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault()
      const nodeType = event.dataTransfer.getData("application/reactflow-node-type") as WorkflowNodeType
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
        data: { ...defaultNodeData[nodeType] },
      }

      setNodes((nds) => [...nds, newNode])
    },
    [setNodes, nodes, t],
  )

  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
  }, [])

  const onNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    setSelectedNodeId(node.id)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null)
    setContextMenu(null)
  }, [])

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault()
      setContextMenu({ type: "node", x: event.clientX, y: event.clientY, nodeId: node.id })
    },
    [],
  )

  const onPaneContextMenu = useCallback((event: MouseEvent | React.MouseEvent) => {
    event.preventDefault()
    setContextMenu({ type: "pane", x: event.clientX, y: event.clientY })
  }, [])

  // Close context menu on scroll or click outside
  useEffect(() => {
    if (!contextMenu) return
    const close = () => setContextMenu(null)
    window.addEventListener("click", close)
    window.addEventListener("scroll", close, true)
    return () => {
      window.removeEventListener("click", close)
      window.removeEventListener("scroll", close, true)
    }
  }, [contextMenu])

  const handleNodeDataUpdate = useCallback(
    (nodeId: string, data: Record<string, unknown>) => {
      setNodes((nds) =>
        nds.map((n) => (n.id === nodeId ? { ...n, data } : n)),
      )
    },
    [setNodes],
  )

  const selectedNode = selectedNodeId
    ? nodes.find((n) => n.id === selectedNodeId) ?? null
    : null

  const handleAutoLayout = useCallback(async () => {
    const layoutedNodes = await getAutoLayoutedNodes(nodes, edges)
    setNodes(layoutedNodes)
    // Wait a tick for React Flow to process the position changes, then fit view
    requestAnimationFrame(() => {
      rfInstanceRef.current?.fitView({ duration: 300, padding: 0.4 })
    })
  }, [nodes, edges, setNodes])

  useImperativeHandle(ref, () => ({
    autoLayout: handleAutoLayout,
    undo: handleUndo,
    redo: handleRedo,
    canUndo,
    canRedo,
  }), [handleAutoLayout, handleUndo, handleRedo, canUndo, canRedo])

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden relative">
      {/* Left palette */}
      <NodePalette existingNodeTypes={existingNodeTypes} />

      {/* Center: React Flow canvas */}
      <div className="flex-1 min-w-0 relative" ref={reactFlowWrapper}>
        <ReactFlow
          nodes={displayNodes}
          edges={displayEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          defaultEdgeOptions={{ type: "default" }}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          snapToGrid
          snapGrid={[16, 16]}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onNodeClick={onNodeClick}
          onNodeContextMenu={onNodeContextMenu}
          onPaneContextMenu={onPaneContextMenu}
          onPaneClick={onPaneClick}
          onInit={(instance) => { rfInstanceRef.current = instance }}
          deleteKeyCode={null}
          fitView
          fitViewOptions={{ maxZoom: 1, padding: 0.4 }}
          colorMode={rfColorMode}
          proOptions={{ hideAttribution: true }}
          minZoom={0.2}
          maxZoom={2}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          <Controls showInteractive={false} />
          <MiniMap
            nodeColor={getMinimapNodeColor}
            nodeStrokeWidth={3}
            pannable
            zoomable
            className="!bg-background/80 !border-border"
          />
        </ReactFlow>

        {/* Node context menu */}
        {contextMenu?.type === "node" && (() => {
          const ctxNode = nodes.find((n) => n.id === contextMenu.nodeId)
          const isStartOrEnd = ctxNode?.type === "start" || ctxNode?.type === "end"
          return (
            <div
              className="fixed z-50 min-w-[160px] rounded-md border border-border bg-popover p-1 shadow-md animate-in fade-in-0 zoom-in-95"
              style={{ left: contextMenu.x, top: contextMenu.y }}
              onClick={(e) => e.stopPropagation()}
            >
              <button
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
                onClick={() => {
                  setSelectedNodeId(contextMenu.nodeId)
                  setContextMenu(null)
                }}
              >
                <Settings className="h-3.5 w-3.5" />
                {t("contextMenuConfigure")}
              </button>
              <button
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
                onClick={() => {
                  if (ctxNode) {
                    copiedNodesRef.current = {
                      nodes: [{ ...ctxNode, data: { ...ctxNode.data } }],
                      edges: [],
                    }
                  }
                  setContextMenu(null)
                }}
              >
                <Copy className="h-3.5 w-3.5" />
                {t("contextMenuCopy")}
              </button>
              <button
                className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
                onClick={() => {
                  handleDuplicateSelected()
                  setContextMenu(null)
                }}
              >
                <Clipboard className="h-3.5 w-3.5" />
                {t("contextMenuDuplicate")}
              </button>
              {!isStartOrEnd && (
                <>
                  <div className="my-1 h-px bg-border" />
                  <button
                    className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-destructive hover:bg-destructive/10 transition-colors"
                    onClick={() => {
                      handleDeleteNode(contextMenu.nodeId)
                      setContextMenu(null)
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("contextMenuDelete")}
                  </button>
                </>
              )}
            </div>
          )
        })()}

        {/* Pane context menu (right-click on empty canvas) */}
        {contextMenu?.type === "pane" && (
          <div
            className="fixed z-50 min-w-[160px] rounded-md border border-border bg-popover p-1 shadow-md animate-in fade-in-0 zoom-in-95"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              disabled={copiedNodesRef.current.nodes.length === 0}
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              onClick={() => {
                handlePaste()
                setContextMenu(null)
              }}
            >
              <Clipboard className="h-3.5 w-3.5" />
              {t("contextMenuPaste")}
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
              onClick={() => {
                handleSelectAll()
                setContextMenu(null)
              }}
            >
              <MousePointerSquareDashed className="h-3.5 w-3.5" />
              {t("contextMenuSelectAll")}
            </button>
            <div className="my-1 h-px bg-border" />
            <button
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
              onClick={() => {
                rfInstanceRef.current?.fitView({ duration: 300, padding: 0.4 })
                setContextMenu(null)
              }}
            >
              <Maximize2 className="h-3.5 w-3.5" />
              {t("contextMenuFitView")}
            </button>
            <button
              className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground hover:bg-accent/50 transition-colors"
              onClick={() => {
                handleAutoLayout()
                setContextMenu(null)
              }}
            >
              <LayoutGrid className="h-3.5 w-3.5" />
              {t("contextMenuAutoLayout")}
            </button>
          </div>
        )}

        {/* Run Panel (overlay at bottom) */}
        <RunPanel
          isOpen={runPanelOpen}
          isRunning={isRunning}
          startVariables={startVariables}
          nodeResults={nodeResults}
          finalOutputs={finalOutputs}
          finalError={finalError}
          runDuration={runDuration}
          nodeTypeMap={nodeTypeMap}
          totalNodeCount={totalNodeCount}
          onStartRun={onStartRun}
          onRunAgain={onRunAgain}
          onCancel={onCancelRun}
          onClose={onCloseRunPanel}
        />
      </div>

      {/* Right config panel */}
      {selectedNodeId && (
        <NodeConfigPanel
          node={selectedNode}
          allNodes={nodes}
          onUpdate={handleNodeDataUpdate}
          onDelete={handleDeleteNode}
          onClose={() => setSelectedNodeId(null)}
        />
      )}

      {/* Keyboard shortcuts help dialog */}
      <KeyboardShortcutsDialog
        open={shortcutsDialogOpen}
        onOpenChange={setShortcutsDialogOpen}
      />
    </div>
  )
})
