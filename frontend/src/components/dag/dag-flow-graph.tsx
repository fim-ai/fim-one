"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  BackgroundVariant,
} from "@xyflow/react"
import type { NodeMouseHandler } from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { Badge } from "@/components/ui/badge"
import { ListTree } from "lucide-react"
import type { DagPhaseEvent } from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import { StepNode } from "./step-node"
import { useDagLayout } from "./use-dag-layout"
import { StepDetailPanel } from "./step-detail-panel"
import type { StepNodeData } from "./types"

// MUST be defined outside the component to prevent ReactFlow infinite re-renders
const nodeTypes = { step: StepNode }

interface DagFlowGraphProps {
  planSteps: NonNullable<DagPhaseEvent["steps"]>
  stepStates: StepState[]
  mode?: "inline" | "sidebar"
  expanded?: boolean
  onStepClick?: (stepId: string) => void
}

export function DagFlowGraph({ planSteps, stepStates, mode = "inline", expanded, onStepClick }: DagFlowGraphProps) {
  const { nodes: layoutNodes, edges: layoutEdges } = useDagLayout({
    planSteps,
    stepStates,
  })

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
  const fitViewFn = useRef<((opts?: { duration?: number }) => void) | null>(null)

  // Re-fit view when sidebar expands/collapses (wait for CSS transition)
  useEffect(() => {
    if (!fitViewFn.current) return
    const timer = setTimeout(() => {
      fitViewFn.current?.({ duration: 300 })
    }, 350)
    return () => clearTimeout(timer)
  }, [expanded])

  // Build a state map for quick lookups
  const stateMap = useMemo(() => {
    const m = new Map<string, StepState>()
    for (const s of stepStates) {
      m.set(s.step_id, s)
    }
    return m
  }, [stepStates])

  // Merge live stepStates into nodes without changing positions
  useEffect(() => {
    setNodes((currentNodes) =>
      currentNodes.map((node) => {
        const state = stateMap.get(node.id)
        if (!state) return node

        const prevData = node.data as unknown as StepNodeData
        const newStatus =
          (state.status as StepNodeData["status"]) ?? "pending"

        // Only update if data actually changed
        if (
          prevData.status === newStatus &&
          prevData.duration === state.duration
        ) {
          return node
        }

        return {
          ...node,
          data: {
            ...node.data,
            status: newStatus,
            duration: state.duration,
            state,
          },
        }
      })
    )
  }, [stateMap, setNodes])

  // When layout changes (new plan), reset nodes and edges entirely
  useEffect(() => {
    setNodes(layoutNodes)
    setEdges(layoutEdges)
  }, [layoutNodes, layoutEdges, setNodes, setEdges])

  const selectedState = selectedStepId
    ? stateMap.get(selectedStepId) ?? null
    : null

  const onNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    if (onStepClick) {
      onStepClick(node.id)
    } else {
      setSelectedStepId((prev) => (prev === node.id ? null : node.id))
    }
  }, [onStepClick])

  const onPaneClick = useCallback(() => {
    setSelectedStepId(null)
  }, [])

  if (mode === "sidebar") {
    return (
      <div className="flex-1 min-h-0 h-full relative">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onInit={(instance) => { fitViewFn.current = instance.fitView }}
          fitView
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={true}
          minZoom={0.3}
          maxZoom={1.5}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    )
  }

  // Inline mode - original behavior
  const graphHeight = Math.max(280, Math.min(planSteps.length * 100 + 80, 600))

  return (
    <div className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 rounded-lg border border-green-500/20 bg-card/50 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/30">
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-500/10">
          <ListTree className="h-3 w-3 text-green-500" />
        </div>
        <span className="text-sm font-medium">Execution Plan</span>
        <Badge variant="secondary" className="text-[10px]">
          {planSteps.length} step{planSteps.length !== 1 ? "s" : ""}
        </Badge>
      </div>

      {/* Graph */}
      <div className="relative" style={{ height: graphHeight }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
          colorMode="dark"
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={true}
          minZoom={0.5}
          maxZoom={1.5}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>

        <StepDetailPanel
          state={selectedState}
          onClose={() => setSelectedStepId(null)}
        />
      </div>
    </div>
  )
}
