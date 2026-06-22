"use client"

import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useReactFlow,
  useNodesData,
} from "@xyflow/react"
import type { EdgeProps, Node } from "@xyflow/react"
import { Plus, X } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import { resolveEdgeLabel } from "./edges/labeled-edge"
import { categories, itemByType } from "./node-palette"
import type { WorkflowNodeType } from "@/types/workflow"

const defaultNodeData: Record<WorkflowNodeType, Record<string, unknown>> = {
  start: { variables: [] },
  end: { output_mapping: {} },
  llm: { prompt_template: "", output_variable: "llm_result", temperature: 0.7 },
  conditionBranch: { mode: "expression", conditions: [] },
  agent: { agent_id: "", output_variable: "agent_result" },
  knowledgeRetrieval: { kb_id: "", query_template: "", top_k: 5, output_variable: "kb_result" },
  connector: { connector_id: "", action: "", parameters: {}, output_variable: "connector_result" },
  humanIntervention: { prompt_message: "", assignee: "", timeout_hours: 24, output_variable: "approval_result" },
  mcp: { server_id: "", tool_name: "", parameters: {}, output_variable: "mcp_result" },
}

export function AddNodeEdge({
  id,
  source,
  target,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  targetHandleId,
  style,
  markerEnd,
}: EdgeProps) {
  const t = useTranslations("workflows")
  const { setEdges, setNodes, getNodes } = useReactFlow()
  const [isHovered, setIsHovered] = useState(false)
  const [showPicker, setShowPicker] = useState(false)
  const [search, setSearch] = useState("")
  const pickerRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const hoverTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Hover is shared between the edge hit-area and the button cluster. A short
  // close delay prevents flicker when the pointer travels from the line to the
  // buttons (they live in separate DOM trees: SVG path vs. HTML portal).
  const handleHoverEnter = useCallback(() => {
    if (hoverTimeoutRef.current) {
      clearTimeout(hoverTimeoutRef.current)
      hoverTimeoutRef.current = null
    }
    setIsHovered(true)
  }, [])

  const handleHoverLeave = useCallback(() => {
    if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current)
    hoverTimeoutRef.current = setTimeout(() => setIsHovered(false), 120)
  }, [])

  useEffect(() => {
    return () => {
      if (hoverTimeoutRef.current) clearTimeout(hoverTimeoutRef.current)
    }
  }, [])

  // Subscribe reactively to source node data so edge labels update when
  // conditions/classes are edited in the config panel
  const sourceNodeData = useNodesData(source)

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  })

  // Resolve edge label from condition/classifier source nodes
  const edgeLabel = useMemo(
    () =>
      resolveEdgeLabel(
        sourceHandleId,
        sourceNodeData?.type,
        sourceNodeData?.data as Record<string, unknown> | undefined,
        t("edgeDefaultLabel"),
      ),
    [sourceHandleId, sourceNodeData, t],
  )

  // Position the label near the source end of the edge (1/4 of the way from source)
  const edgeLabelX = sourceX + (labelX - sourceX) * 0.45
  const edgeLabelY = sourceY + (labelY - sourceY) * 0.45

  const closePicker = useCallback(() => {
    setShowPicker(false)
    setSearch("")
  }, [])

  // Close picker when clicking outside or pressing Escape
  useEffect(() => {
    if (!showPicker) return

    const handlePointerDown = (e: PointerEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as HTMLElement)) {
        closePicker()
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closePicker()
      }
    }

    document.addEventListener("pointerdown", handlePointerDown)
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown)
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [showPicker, closePicker])

  // Auto-focus search input when picker opens
  useEffect(() => {
    if (showPicker) {
      // Small delay to ensure the DOM is rendered
      requestAnimationFrame(() => {
        searchInputRef.current?.focus()
      })
    }
  }, [showPicker])

  const handleAddNode = useCallback(
    (nodeType: WorkflowNodeType) => {
      closePicker()

      const newNodeId = `${nodeType}_${Date.now()}`
      const midX = (sourceX + targetX) / 2
      const midY = (sourceY + targetY) / 2

      const newNode: Node = {
        id: newNodeId,
        type: nodeType,
        position: { x: midX - 110, y: midY - 30 },
        data: { ...defaultNodeData[nodeType] },
      }

      // Remove the current edge and add two new edges
      setNodes((nodes) => [...nodes, newNode])
      setEdges((edges) => {
        const filtered = edges.filter((e) => e.id !== id)
        return [
          ...filtered,
          {
            id: `e-${source}-${sourceHandleId ?? "default"}-${newNodeId}-target`,
            source,
            target: newNodeId,
            sourceHandle: sourceHandleId ?? undefined,
            targetHandle: "target",
          },
          {
            id: `e-${newNodeId}-source-${target}-${targetHandleId ?? "default"}`,
            source: newNodeId,
            target,
            sourceHandle: "source",
            targetHandle: targetHandleId ?? undefined,
          },
        ]
      })
    },
    [id, source, target, sourceHandleId, targetHandleId, sourceX, sourceY, targetX, targetY, setEdges, setNodes, closePicker],
  )

  const handleDeleteEdge = useCallback(() => {
    setEdges((edges) => edges.filter((e) => e.id !== id))
  }, [id, setEdges])

  // Check which node types already exist as single-instance (start/end)
  const existingNodes = getNodes()
  const hasStart = existingNodes.some((n) => n.type === "start")
  const hasEnd = existingNodes.some((n) => n.type === "end")

  const searchLower = search.toLowerCase()

  /** Check if a node type matches the search query */
  const matchesSearch = useCallback(
    (type: WorkflowNodeType): boolean => {
      if (!searchLower) return true
      const name = t(`nodeType_${type}` as Parameters<typeof t>[0]).toLowerCase()
      return name.includes(searchLower) || type.toLowerCase().includes(searchLower)
    },
    [searchLower, t],
  )

  /** Filter out singleton types and apply search */
  const isSingletonBlocked = useCallback(
    (type: WorkflowNodeType): boolean => {
      if (type === "start" && hasStart) return true
      if (type === "end" && hasEnd) return true
      return false
    },
    [hasStart, hasEnd],
  )

  return (
    <>
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: "var(--muted-foreground)",
          strokeWidth: 2,
        }}
      />
      {/* Transparent wide hit-area: reveal the add/cut buttons on hover
          anywhere along the line, not just at the midpoint. */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        style={{ pointerEvents: "stroke", cursor: "pointer" }}
        onMouseEnter={handleHoverEnter}
        onMouseLeave={handleHoverLeave}
      />
      <EdgeLabelRenderer>
        {/* Edge label for condition/classifier branches */}
        {edgeLabel && (
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{
              transform: `translate(-50%, -50%) translate(${edgeLabelX}px, ${edgeLabelY}px)`,
            }}
          >
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-muted border border-border text-muted-foreground whitespace-nowrap">
              {edgeLabel}
            </span>
          </div>
        )}
        <div
          className="nodrag nopan pointer-events-auto absolute"
          style={{
            transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
          }}
          onMouseEnter={handleHoverEnter}
          onMouseLeave={handleHoverLeave}
        >
          <div className="flex items-center gap-1">
            {/* Delete edge button */}
            <button
              className={cn(
                "flex h-5 w-5 items-center justify-center rounded-full border bg-background shadow-sm transition-all duration-150",
                "hover:bg-destructive hover:text-destructive-foreground hover:border-destructive hover:scale-110",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-destructive",
                (isHovered || showPicker) ? "opacity-100 scale-100" : "opacity-0 scale-75",
              )}
              onClick={handleDeleteEdge}
            >
              <X className="h-3 w-3" />
            </button>
            {/* Plus button */}
            <button
              className={cn(
                "flex h-5 w-5 items-center justify-center rounded-full border bg-background shadow-sm transition-all duration-150",
                "hover:bg-primary hover:text-primary-foreground hover:border-primary hover:scale-110",
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                (isHovered || showPicker) ? "opacity-100 scale-100" : "opacity-0 scale-75",
              )}
              onClick={() => {
                if (showPicker) {
                  closePicker()
                } else {
                  setShowPicker(true)
                }
              }}
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>

          {/* Node type picker */}
          {showPicker && (
            <div
              ref={pickerRef}
              className="absolute top-7 left-1/2 -translate-x-1/2 z-50 w-[200px] rounded-lg border border-border bg-popover shadow-lg"
              onPointerDown={(e) => e.stopPropagation()}
            >
              {/* Header */}
              <div className="flex items-center justify-between px-2.5 pt-2 pb-1">
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {t("addNodePickerTitle")}
                </p>
                <button
                  className={cn(
                    "flex h-4 w-4 items-center justify-center rounded-sm text-muted-foreground transition-colors",
                    "hover:bg-accent hover:text-foreground",
                    "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                  )}
                  onClick={closePicker}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>

              {/* Search input */}
              <div className="px-2 pb-1.5">
                <input
                  ref={searchInputRef}
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("addNodePickerSearch")}
                  className={cn(
                    "w-full h-6 rounded-md border border-border bg-background px-2 text-[11px] text-foreground placeholder:text-muted-foreground/50",
                    "focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-primary",
                  )}
                />
              </div>

              {/* Category list */}
              <div className="max-h-[260px] overflow-y-auto px-1 pb-1.5">
                {categories.map((cat) => {
                  const filtered = cat.items.filter(
                    (item) => !isSingletonBlocked(item.type) && matchesSearch(item.type),
                  )
                  if (filtered.length === 0) return null
                  return (
                    <div key={cat.key} className="mt-1 first:mt-0">
                      <p className="px-1.5 py-0.5 text-[9px] font-semibold text-muted-foreground/60 uppercase tracking-wider">
                        {t(cat.key as Parameters<typeof t>[0])}
                      </p>
                      {filtered.map((item) => {
                        const paletteItem = itemByType[item.type]
                        return (
                          <button
                            key={item.type}
                            className={cn(
                              "flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-xs text-foreground transition-colors",
                              "hover:bg-accent/50",
                              "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                            )}
                            onClick={() => handleAddNode(item.type)}
                          >
                            <span className={cn("shrink-0", paletteItem?.color ?? item.color)}>
                              {paletteItem?.icon}
                            </span>
                            <span className="truncate text-[11px]">
                              {t(`nodeType_${item.type}` as Parameters<typeof t>[0])}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  )
                })}

                {/* No results */}
                {categories.every((cat) =>
                  cat.items.every(
                    (item) => isSingletonBlocked(item.type) || !matchesSearch(item.type),
                  ),
                ) && (
                  <p className="text-[10px] text-muted-foreground text-center py-3">
                    {t("noNodeSearchResults")}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
