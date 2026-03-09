"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslations } from "next-intl"
import { useTheme } from "next-themes"
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  BackgroundVariant,
} from "@xyflow/react"
import type { NodeChange, NodeMouseHandler } from "@xyflow/react"
import "@xyflow/react/dist/style.css"

import { Badge } from "@/components/ui/badge"
import { ListTree } from "lucide-react"
import type { DagPhaseEvent } from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import { StepNode } from "./step-node"
import { useDagLayout } from "./use-dag-layout"
import { StepDetailPanel } from "./step-detail-panel"
import type { StepFlowNode, StepNodeData } from "./types"

// MUST be defined outside the component to prevent ReactFlow infinite re-renders
const nodeTypes = { step: StepNode }

interface DagFlowGraphProps {
  planSteps: NonNullable<DagPhaseEvent["steps"]>
  stepStates: StepState[]
  mode?: "inline" | "sidebar"
  expanded?: boolean
  resizeKey?: number
  onStepClick?: (stepId: string) => void
}

export function DagFlowGraph({ planSteps, stepStates, mode = "inline", expanded, resizeKey, onStepClick }: DagFlowGraphProps) {
  const t = useTranslations("dag")
  const { resolvedTheme } = useTheme()
  const rfColorMode = resolvedTheme === "dark" ? "dark" : "light"

  const { nodes: layoutNodes, edges: layoutEdges, dagreCenters } = useDagLayout(planSteps)

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layoutEdges)
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
  const fitViewFn = useRef<((opts?: { duration?: number }) => void) | null>(null)

  // Stable ref for Dagre center-Y values (used in dimension-change handler)
  const dagreCentersRef = useRef(dagreCenters)
  dagreCentersRef.current = dagreCenters

  // Timer ref for debouncing fitView after dimension changes
  const fitAfterDimTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Only fitView once on initial dimension measurement, not during streaming
  const initialFitDone = useRef(false)

  // Intercept dimension changes to center-align nodes based on measured height
  const handleNodesChange = useCallback(
    (changes: NodeChange<StepFlowNode>[]) => {
      onNodesChange(changes)

      const hasDimChange = changes.some((c) => c.type === "dimensions")
      if (!hasDimChange) return

      setNodes((currentNodes) => {
        let changed = false
        const next = currentNodes.map((node) => {
          const centerY = dagreCentersRef.current.get(node.id)
          if (centerY === undefined) return node
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const h = (node as any).measured?.height as number | undefined
          if (!h) return node
          const newY = centerY - h / 2
          if (Math.abs(node.position.y - newY) < 0.5) return node
          changed = true
          return { ...node, position: { ...node.position, y: newY } }
        })
        return changed ? next : currentNodes
      })

      // Re-fit only once after initial dimension measurement
      if (!initialFitDone.current) {
        if (fitAfterDimTimer.current) clearTimeout(fitAfterDimTimer.current)
        fitAfterDimTimer.current = setTimeout(() => {
          initialFitDone.current = true
          fitViewFn.current?.({ duration: 200 })
        }, 100)
      }
    },
    [onNodesChange, setNodes],
  )

  // Re-fit view when sidebar expands/collapses (wait for CSS transition)
  useEffect(() => {
    if (!fitViewFn.current) return
    const timer = setTimeout(() => {
      fitViewFn.current?.({ duration: 300 })
    }, 350)
    return () => clearTimeout(timer)
  }, [expanded])

  // Re-fit after drag resize ends
  useEffect(() => {
    if (resizeKey === undefined || resizeKey === 0) return
    const timer = setTimeout(() => {
      fitViewFn.current?.({ duration: 200 })
    }, 50)
    return () => clearTimeout(timer)
  }, [resizeKey])

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
        const toolsLen = state.tools_used?.length ?? 0
        const prevToolsLen = prevData.tools_used?.length ?? 0

        // Only update if data actually changed
        if (
          prevData.status === newStatus &&
          prevData.duration === state.duration &&
          prevData.started_at === state.started_at &&
          prevToolsLen === toolsLen
        ) {
          return node
        }

        return {
          ...node,
          data: {
            ...node.data,
            status: newStatus,
            duration: state.duration,
            started_at: state.started_at,
            tools_used: state.tools_used,
            state,
          },
        }
      })
    )
  }, [stateMap, setNodes])

  // When layout changes (new plan), reset nodes and edges entirely
  useEffect(() => {
    initialFitDone.current = false
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
          onNodesChange={handleNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onInit={(instance) => { fitViewFn.current = instance.fitView }}
          fitView
          colorMode={rfColorMode}
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
  const graphHeight = Math.max(380, Math.min(planSteps.length * 120 + 100, 600))

  return (
    <div className="rounded-lg border border-green-500/20 bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/30">
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-500/10">
          <ListTree className="h-3 w-3 text-green-500" />
        </div>
        <span className="text-sm font-medium">{t("executionPlan")}</span>
        <Badge variant="secondary" className="text-[10px]">
          {t("stepCount", { count: planSteps.length })}
        </Badge>
      </div>

      {/* Graph */}
      <div className="relative" style={{ height: graphHeight }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={handleNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          onInit={(instance) => { fitViewFn.current = instance.fitView }}
          fitView
          colorMode={rfColorMode}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={true}
          zoomOnScroll={false}
          panOnScroll={false}
          preventScrolling={false}
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
