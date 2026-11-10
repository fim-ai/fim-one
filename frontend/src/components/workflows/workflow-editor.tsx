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
  BackgroundVariant,
} from "@xyflow/react"
import type {
  NodeMouseHandler,
  Node,
  EdgeTypes,
  ReactFlowInstance,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { useTranslations } from "next-intl"
import { X } from "lucide-react"

import { useRecentNodes } from "@/hooks/use-recent-nodes"
import { useModuleFlags } from "@/lib/module-flags"
import { getAutoLayoutedNodes } from "./auto-layout"
import { HiddenNodeTypesContext, NodePalette } from "./node-palette"
import { NodeConfigPanel } from "./node-config-panel"
import { TestNodeDialog } from "./test-node-dialog"
import { RunPanel } from "./run-panel"
import { AddNodeEdge } from "./add-node-edge"
import { KeyboardShortcutsDialog } from "./keyboard-shortcuts-dialog"
import { CanvasSearchBar } from "./canvas-search-bar"
import {
  blueprintEdges,
  blueprintNodes,
  highlightSearch,
  nodeTypesOnCanvas,
  withLiveRunStatus,
  withRunAnimation,
} from "./editor/canvas-model"
import { NodeContextMenu, PaneContextMenu, useCanvasContextMenu } from "./editor/canvas-context-menus"
import { useBlueprintSync } from "./editor/use-blueprint-sync"
import { useCanvasConnections } from "./editor/use-canvas-connections"
import { useCanvasEditing } from "./editor/use-canvas-editing"
import { useCanvasHistory } from "./editor/use-canvas-history"
import { useCanvasOverlays } from "./editor/use-canvas-overlays"
import { useCanvasSearch } from "./editor/use-canvas-search"
import { useCanvasShortcuts } from "./editor/use-canvas-shortcuts"
import type {
  WorkflowBlueprint,
  WorkflowNodeType,
  StartNodeData,
  NodeRunResult,
  WorkflowLogEvent,
} from "@/types/workflow"
import type { ValidationResult } from "@/components/workflows/workflow-toolbar"

import { StartNode } from "./nodes/start-node"
import { EndNode } from "./nodes/end-node"
import { LLMNode } from "./nodes/llm-node"
import { ConditionBranchNode } from "./nodes/condition-branch-node"
import { AgentNode } from "./nodes/agent-node"
import { KnowledgeRetrievalNode } from "./nodes/knowledge-retrieval-node"
import { ConnectorNode } from "./nodes/connector-node"
import { HumanInterventionNode } from "./nodes/human-intervention-node"
import { MCPNode } from "./nodes/mcp-node"

// MUST be defined outside the component to prevent ReactFlow infinite re-renders
const nodeTypes = {
  start: StartNode,
  end: EndNode,
  llm: LLMNode,
  conditionBranch: ConditionBranchNode,
  agent: AgentNode,
  knowledgeRetrieval: KnowledgeRetrievalNode,
  connector: ConnectorNode,
  humanIntervention: HumanInterventionNode,
  mcp: MCPNode,
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
  agent: "#6366f1",
  knowledgeRetrieval: "#14b8a6",
  conditionBranch: "#f97316",
  connector: "#a855f7",
  humanIntervention: "#0ea5e9",
  mcp: "#8b5cf6",
}

const getMinimapNodeColor = (node: Node) => minimapNodeColor[node.type ?? ""] ?? "#6b7280"


interface WorkflowEditorProps {
  workflowId: string
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
  logEvents: WorkflowLogEvent[]
  onStartRun: (inputs: Record<string, unknown>) => void
  onRunAgain: () => void
  onCancelRun: () => void
  onCloseRunPanel: () => void
}

export interface WorkflowEditorHandle {
  /** The canvas as a blueprint, as it is right now. */
  getBlueprint: () => WorkflowBlueprint
  autoLayout: () => void
  undo: () => void
  redo: () => void
  canUndo: boolean
  canRedo: boolean
  applyRunOverlay: (nodeResults: Record<string, NodeRunResult>) => void
  clearRunOverlay: () => void
  applyValidationOverlay: (result: ValidationResult) => void
  clearValidationOverlay: () => void
}

export const WorkflowEditor = forwardRef<WorkflowEditorHandle, WorkflowEditorProps>(function WorkflowEditor({
  workflowId,
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
  logEvents,
  onStartRun,
  onRunAgain,
  onCancelRun,
  onCloseRunPanel,
}, ref) {
  const t = useTranslations("workflows")
  const { resolvedTheme } = useTheme()
  const rfColorMode = resolvedTheme === "dark" ? "dark" : "light"
  const reactFlowWrapper = useRef<HTMLDivElement>(null)
  const { recentNodes, addRecentNode } = useRecentNodes()
  const rfInstanceRef = useRef<ReactFlowInstance | null>(null)

  // Node types the instance does not offer for adding. Existing nodes of a
  // hidden type still render, so their author can remove them.
  const { humanIntervention } = useModuleFlags()
  const hiddenNodeTypes = useMemo(
    () => new Set<WorkflowNodeType>(humanIntervention ? [] : ["humanIntervention"]),
    [humanIntervention],
  )

  // --- The graph: the canvas as loaded, then React Flow's node and edge state ---
  const [initial] = useState(() => ({
    nodes: blueprintNodes(blueprint),
    edges: blueprintEdges(blueprint),
  }))
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges)

  // --- UI state that is not part of the graph ---
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [contextMenu, setContextMenu] = useCanvasContextMenu()
  const [shortcutsDialogOpen, setShortcutsDialogOpen] = useState(false)
  const [testNodeTarget, setTestNodeTarget] = useState<{ nodeId: string; nodeType: WorkflowNodeType; label: string } | null>(null)

  // --- One hook per concern ---
  const history = useCanvasHistory({
    nodes,
    edges,
    setNodes,
    setEdges,
    initialNodes: initial.nodes,
    initialEdges: initial.edges,
  })
  const editing = useCanvasEditing({
    nodes,
    edges,
    setNodes,
    setEdges,
    selectedNodeId,
    setSelectedNodeId,
  })
  const search = useCanvasSearch(nodes, rfInstanceRef)
  const overlays = useCanvasOverlays(setNodes)
  const connections = useCanvasConnections({
    nodes,
    edges,
    setNodes,
    setEdges,
    rfInstanceRef,
    onNodeAdded: addRecentNode,
  })
  const sync = useBlueprintSync(nodes, edges, blueprint.viewport, onBlueprintChange)
  const getBlueprint = sync.serialize

  const showShortcuts = useCallback(() => setShortcutsDialogOpen(true), [])
  const clearSelection = useCallback(() => {
    setNodes((nds) => nds.map((n) => (n.selected ? { ...n, selected: false } : n)))
    setSelectedNodeId(null)
  }, [setNodes])
  useCanvasShortcuts({
    undo: history.undo,
    redo: history.redo,
    deleteSelected: editing.deleteSelected,
    copySelected: editing.copySelected,
    paste: editing.paste,
    selectAll: editing.selectAll,
    duplicateSelected: editing.duplicateSelected,
    showShortcuts,
    searchOpen: search.open,
    openSearch: search.openSearch,
    closeSearch: search.closeSearch,
    clearSelection,
  })

  // Notify parent of undo/redo state changes
  const { canUndo, canRedo } = history
  useEffect(() => {
    onUndoRedoChange?.(canUndo, canRedo)
  }, [canUndo, canRedo, onUndoRedoChange])

  // Track which node types exist for palette dimming
  const existingNodeTypes = useMemo(() => nodeTypesOnCanvas(nodes), [nodes])

  // Derived nodes with run status + overlay + search highlighting merged — pure derivation, no state mutation
  const showRunStatus = nodeResults != null && (runPanelOpen || isRunning)
  const displayNodes = useMemo(() => {
    const withStatus = showRunStatus && nodeResults ? withLiveRunStatus(nodes, nodeResults) : nodes
    return highlightSearch(withStatus, {
      active: search.active,
      matchIds: search.matchIds,
      currentId: search.currentId,
    })
  }, [nodes, nodeResults, showRunStatus, search.active, search.matchIds, search.currentId])

  // Derived edges: animate when source is completed and target is running
  const displayEdges = useMemo(() => {
    if (!showRunStatus || !nodeResults) return edges
    return withRunAnimation(edges, nodeResults)
  }, [edges, nodeResults, showRunStatus])

  const onNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    setSelectedNodeId(node.id)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null)
    setContextMenu(null)
  }, [setContextMenu])

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      event.preventDefault()
      setContextMenu({ type: "node", x: event.clientX, y: event.clientY, nodeId: node.id })
    },
    [setContextMenu],
  )

  const onPaneContextMenu = useCallback((event: MouseEvent | React.MouseEvent) => {
    event.preventDefault()
    setContextMenu({ type: "pane", x: event.clientX, y: event.clientY })
  }, [setContextMenu])

  const handleNodeDataUpdate = useCallback(
    (nodeId: string, data: Record<string, unknown>) => {
      // When a node is edited, clear its validation state (stale results)
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { _validationState: _discarded, ...cleanData } = data
      setNodes((nds) =>
        nds.map((n) => (n.id === nodeId ? { ...n, data: cleanData } : n)),
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

  const { undo, redo } = history
  const { applyRunOverlay, clearRunOverlay, applyValidationOverlay, clearValidationOverlay } = overlays
  useImperativeHandle(ref, () => ({
    getBlueprint,
    autoLayout: handleAutoLayout,
    undo,
    redo,
    canUndo,
    canRedo,
    applyRunOverlay,
    clearRunOverlay,
    applyValidationOverlay,
    clearValidationOverlay,
  }), [getBlueprint, handleAutoLayout, undo, redo, canUndo, canRedo, applyRunOverlay, clearRunOverlay, applyValidationOverlay, clearValidationOverlay])

  const contextNode =
    contextMenu?.type === "node" ? nodes.find((n) => n.id === contextMenu.nodeId) : undefined

  return (
    <HiddenNodeTypesContext.Provider value={hiddenNodeTypes}>
    <div className="flex flex-1 min-h-0 overflow-hidden relative">
      {/* Left palette */}
      <NodePalette existingNodeTypes={existingNodeTypes} recentNodes={recentNodes} />

      {/* Center: React Flow canvas */}
      <div
        className="flex-1 min-w-0 relative"
        ref={reactFlowWrapper}
        // For tests: whether the canvas has handed its last change to the
        // page and to undo history (both follow a change after a pause).
        data-testid="workflow-canvas"
        data-sync-pending={sync.pending}
        data-sync-reports={sync.reports}
        data-history-pending={history.pending}
        data-history-revision={history.revision}
      >
        <ReactFlow
          nodes={displayNodes}
          edges={displayEdges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          defaultEdgeOptions={{ type: "default" }}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={connections.onConnect}
          isValidConnection={connections.isValidConnection}
          snapToGrid
          snapGrid={[16, 16]}
          onDrop={connections.onDrop}
          onDragOver={connections.onDragOver}
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

        {/* Canvas search bar */}
        <CanvasSearchBar
          open={search.open}
          query={search.query}
          matchCount={search.matchCount}
          currentIndex={search.index}
          onQueryChange={search.changeQuery}
          onNext={search.next}
          onPrev={search.prev}
          onClose={search.closeSearch}
        />

        {/* Run overlay banner */}
        {overlays.hasRunOverlay && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-card/95 backdrop-blur shadow-sm">
            <span className="text-xs text-muted-foreground">{t("runReplayActive")}</span>
            <button
              type="button"
              onClick={clearRunOverlay}
              className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              {t("clearRunOverlay")}
              <X className="h-3 w-3" />
            </button>
          </div>
        )}

        {/* Node context menu */}
        {contextMenu?.type === "node" && (
          <NodeContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            canDelete={!(contextNode?.type === "start" || contextNode?.type === "end")}
            onConfigure={() => {
              setSelectedNodeId(contextMenu.nodeId)
              setContextMenu(null)
            }}
            onTest={() => {
              if (contextNode) {
                const nType = contextNode.type as WorkflowNodeType
                setTestNodeTarget({
                  nodeId: contextNode.id,
                  nodeType: nType,
                  label: t(`nodeType_${nType}` as Parameters<typeof t>[0]),
                })
              }
              setContextMenu(null)
            }}
            onCopy={() => {
              if (contextNode) editing.copyNode(contextNode)
              setContextMenu(null)
            }}
            onDuplicate={() => {
              editing.duplicateNode(contextMenu.nodeId)
              setContextMenu(null)
            }}
            onDelete={() => {
              editing.deleteNode(contextMenu.nodeId)
              setContextMenu(null)
            }}
          />
        )}

        {/* Pane context menu (right-click on empty canvas) */}
        {contextMenu?.type === "pane" && (
          <PaneContextMenu
            x={contextMenu.x}
            y={contextMenu.y}
            canPaste={editing.hasClipboard()}
            onPaste={() => {
              editing.paste()
              setContextMenu(null)
            }}
            onSelectAll={() => {
              editing.selectAll()
              setContextMenu(null)
            }}
            onFitView={() => {
              rfInstanceRef.current?.fitView({ duration: 300, padding: 0.4 })
              setContextMenu(null)
            }}
            onAutoLayout={() => {
              handleAutoLayout()
              setContextMenu(null)
            }}
          />
        )}

      </div>

      {/* Right sidebar: Run Panel takes priority over Node Config */}
      {runPanelOpen ? (
        <RunPanel
          isOpen={runPanelOpen}
          isRunning={isRunning}
          startVariables={startVariables ?? []}
          nodeResults={nodeResults}
          finalOutputs={finalOutputs}
          finalError={finalError}
          runDuration={runDuration}
          nodeTypeMap={nodeTypeMap}
          totalNodeCount={totalNodeCount}
          logEvents={logEvents}
          onStartRun={onStartRun}
          onRunAgain={onRunAgain}
          onCancel={onCancelRun}
          onClose={onCloseRunPanel}
        />
      ) : selectedNodeId ? (
        <NodeConfigPanel
          workflowId={workflowId}
          node={selectedNode}
          allNodes={nodes}
          onUpdate={handleNodeDataUpdate}
          onDelete={editing.deleteNode}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : null}

      {/* Keyboard shortcuts help dialog */}
      <KeyboardShortcutsDialog
        open={shortcutsDialogOpen}
        onOpenChange={setShortcutsDialogOpen}
      />

      {/* Test node dialog (opened from context menu) */}
      {testNodeTarget && (
        <TestNodeDialog
          workflowId={workflowId}
          nodeId={testNodeTarget.nodeId}
          nodeType={testNodeTarget.nodeType}
          nodeLabel={testNodeTarget.label}
          open={!!testNodeTarget}
          onOpenChange={(open) => {
            if (!open) setTestNodeTarget(null)
          }}
        />
      )}
    </div>
    </HiddenNodeTypesContext.Provider>
  )
})
