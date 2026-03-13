"use client"

import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useReactFlow,
  useNodesData,
} from "@xyflow/react"
import type { EdgeProps, Node } from "@xyflow/react"
import { Plus, X } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import { resolveEdgeLabel } from "./edges/labeled-edge"
import type { WorkflowNodeType } from "@/types/workflow"

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

const nodeTypeOptions: { type: WorkflowNodeType; color: string }[] = [
  { type: "llm", color: "text-blue-500" },
  { type: "conditionBranch", color: "text-orange-500" },
  { type: "questionClassifier", color: "text-teal-500" },
  { type: "agent", color: "text-indigo-500" },
  { type: "knowledgeRetrieval", color: "text-teal-500" },
  { type: "connector", color: "text-purple-500" },
  { type: "httpRequest", color: "text-slate-500" },
  { type: "variableAssign", color: "text-gray-500" },
  { type: "templateTransform", color: "text-amber-500" },
  { type: "codeExecution", color: "text-emerald-500" },
  { type: "iterator", color: "text-cyan-500" },
  { type: "loop", color: "text-orange-500" },
  { type: "variableAggregator", color: "text-sky-500" },
  { type: "parameterExtractor", color: "text-violet-500" },
  { type: "listOperation", color: "text-lime-500" },
  { type: "transform", color: "text-rose-500" },
  { type: "documentExtractor", color: "text-amber-600" },
  { type: "questionUnderstanding", color: "text-pink-500" },
  { type: "humanIntervention", color: "text-sky-500" },
  { type: "mcp", color: "text-violet-500" },
  { type: "builtinTool", color: "text-zinc-500" },
  { type: "subWorkflow", color: "text-indigo-500" },
  { type: "env", color: "text-amber-600" },
]

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
  const pickerRef = useRef<HTMLDivElement>(null)

  // Subscribe reactively to source node data so edge labels update when
  // conditions/classes are edited in the config panel
  const sourceNodeData = useNodesData(source)

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
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

  // Close picker when clicking outside
  useEffect(() => {
    if (!showPicker) return
    const handleClickOutside = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as HTMLElement)) {
        setShowPicker(false)
      }
    }
    document.addEventListener("mousedown", handleClickOutside)
    return () => document.removeEventListener("mousedown", handleClickOutside)
  }, [showPicker])

  const handleAddNode = useCallback(
    (nodeType: WorkflowNodeType) => {
      setShowPicker(false)

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
    [id, source, target, sourceHandleId, targetHandleId, sourceX, sourceY, targetX, targetY, setEdges, setNodes],
  )

  const handleDeleteEdge = useCallback(() => {
    setEdges((edges) => edges.filter((e) => e.id !== id))
  }, [id, setEdges])

  // Check which node types already exist as single-instance (start/end)
  const existingNodes = getNodes()
  const hasStart = existingNodes.some((n) => n.type === "start")
  const hasEnd = existingNodes.some((n) => n.type === "end")

  return (
    <>
      <BaseEdge
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          ...style,
          stroke: "hsl(var(--border))",
          strokeWidth: 1.5,
        }}
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
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => {
            if (!showPicker) setIsHovered(false)
          }}
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
              onClick={() => setShowPicker(!showPicker)}
            >
              <Plus className="h-3 w-3" />
            </button>
          </div>

          {/* Node type picker */}
          {showPicker && (
            <div
              ref={pickerRef}
              className="absolute top-7 left-1/2 -translate-x-1/2 z-50 w-[180px] rounded-md border border-border bg-popover p-1 shadow-md"
            >
              <p className="px-2 py-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                {t("addNodePickerTitle")}
              </p>
              <div className="max-h-[240px] overflow-y-auto">
                {nodeTypeOptions
                  .filter((opt) => {
                    if (opt.type === "start" && hasStart) return false
                    if (opt.type === "end" && hasEnd) return false
                    return true
                  })
                  .map((opt) => (
                    <button
                      key={opt.type}
                      className={cn(
                        "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs text-foreground transition-colors",
                        "hover:bg-accent/50",
                        "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-primary",
                      )}
                      onClick={() => handleAddNode(opt.type)}
                    >
                      <span className={cn("text-[10px]", opt.color)}>
                        {t(`nodeType_${opt.type}` as Parameters<typeof t>[0])}
                      </span>
                    </button>
                  ))}
              </div>
            </div>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
