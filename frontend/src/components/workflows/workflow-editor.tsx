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

import { useWorkflowHistory } from "@/hooks/use-workflow-history"
import { getAutoLayoutedNodes } from "./auto-layout"
import { NodePalette } from "./node-palette"
import { NodeConfigPanel } from "./node-config-panel"
import { RunPanel } from "./run-panel"
import { AddNodeEdge } from "./add-node-edge"
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
}

// Custom edge types - defined outside component for stability
const edgeTypes: EdgeTypes = {
  default: AddNodeEdge,
}

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

  // Keyboard shortcuts: Cmd+Z = undo, Cmd+Shift+Z = redo, Backspace/Delete = delete selected
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't intercept when typing in an input/textarea
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

      // Undo/Redo: Cmd+Z / Cmd+Shift+Z
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") {
        e.preventDefault()
        if (e.shiftKey) {
          handleRedo()
        } else {
          handleUndo()
        }
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
  }, [handleUndo, handleRedo, handleDeleteSelected])

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

  // Sync node run results into node data
  useEffect(() => {
    if (!nodeResults) return
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        const result = nodeResults[node.id]
        const prevStatus = node.data.runStatus
        const newStatus = result?.status
        if (prevStatus === newStatus) return node
        return {
          ...node,
          data: { ...node.data, runStatus: newStatus },
        }
      }),
    )
  }, [nodeResults, setNodes])

  // Clear run status when run panel closes and no run is active
  useEffect(() => {
    if (!runPanelOpen && !isRunning) {
      setNodes((currentNodes) =>
        currentNodes.map((node) => {
          if (!node.data.runStatus) return node
          return { ...node, data: { ...node.data, runStatus: undefined } }
        }),
      )
    }
  }, [runPanelOpen, isRunning, setNodes])

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
      const targetNode = nodes.find((n) => n.id === connection.target)
      const sourceNode = nodes.find((n) => n.id === connection.source)
      if (targetNode?.type === "start") return false
      if (sourceNode?.type === "end") return false
      return true
    },
    [nodes],
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

      const wrapper = reactFlowWrapper.current
      if (!wrapper) return

      const bounds = wrapper.getBoundingClientRect()
      const position = {
        x: event.clientX - bounds.left - 110,
        y: event.clientY - bounds.top - 30,
      }

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
  }, [])

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
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          defaultEdgeOptions={{ type: "default" }}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          isValidConnection={isValidConnection}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onNodeClick={onNodeClick}
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
            nodeStrokeWidth={3}
            pannable
            zoomable
            className="!bg-background/80 !border-border"
          />
        </ReactFlow>

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
    </div>
  )
})
