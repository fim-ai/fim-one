"use client"

import { memo, useMemo } from "react"
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useNodesData,
} from "@xyflow/react"
import type { EdgeProps } from "@xyflow/react"
import { useTranslations } from "next-intl"
import type {
  ConditionNodeData,
  QuestionClassifierNodeData,
} from "@/types/workflow"

/**
 * Resolves a human-readable edge label from the sourceHandle ID and
 * the source node's type + data.
 *
 * Returns `null` when no label applies (source is not a condition /
 * classifier node, or the handle cannot be matched).
 *
 * Exported so that `AddNodeEdge` can reuse the same logic without
 * duplicating the condition/classifier parsing.
 */
export function resolveEdgeLabel(
  sourceHandleId: string | null | undefined,
  sourceNodeType: string | undefined,
  sourceNodeData: Record<string, unknown> | undefined,
  defaultLabel: string,
): string | null {
  if (!sourceHandleId || !sourceNodeData) return null

  if (sourceNodeType === "conditionBranch") {
    const nodeData = sourceNodeData as unknown as ConditionNodeData
    const conditions = nodeData.conditions ?? []
    // sourceHandle format: "condition-{id}"
    const conditionId = sourceHandleId.replace(/^condition-/, "")
    const matched = conditions.find((c) => c.id === conditionId)
    if (matched) return matched.label || null
    // Fallback for default handle
    if (sourceHandleId === "source-default") return defaultLabel
    return null
  }

  if (sourceNodeType === "questionClassifier") {
    const nodeData = sourceNodeData as unknown as QuestionClassifierNodeData
    const classes = nodeData.classes ?? []
    // sourceHandle format: "class-{id}"
    const classId = sourceHandleId.replace(/^class-/, "")
    const matched = classes.find((c) => c.id === classId)
    if (matched) return matched.label || null
    return null
  }

  return null
}

/**
 * A lightweight custom React Flow edge that renders a smooth-step curve
 * and, for condition-branch / question-classifier source nodes, displays
 * a small pill label near the source end of the edge.
 *
 * For all other node types the edge renders as a plain curve with no
 * label (identical visual weight to the built-in default edge).
 *
 * This component is intentionally kept separate from `AddNodeEdge` so
 * it can be used as a read-only labelled edge without the add-node-on-
 * edge interaction (e.g. in a preview / read-only mode).
 */
function LabeledEdgeComponent({
  source,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  sourceHandleId,
  style,
  markerEnd,
}: EdgeProps) {
  const t = useTranslations("workflows")

  // Subscribe reactively to source node data so labels update when
  // conditions / classes are edited in the config panel.
  // `useNodesData` only triggers a re-render when the *data* of
  // the subscribed node changes — keeps this component lightweight.
  const sourceNodeData = useNodesData(source)

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

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 8,
  })

  // Position the label near the source end (45% of the way from source
  // to the geometric midpoint) so it sits close to the originating handle.
  const edgeLabelX = sourceX + (labelX - sourceX) * 0.45
  const edgeLabelY = sourceY + (labelY - sourceY) * 0.45

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
      {edgeLabel && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan pointer-events-none absolute"
            style={{
              transform: `translate(-50%, -50%) translate(${edgeLabelX}px, ${edgeLabelY}px)`,
            }}
          >
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border whitespace-nowrap">
              {edgeLabel}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

export const LabeledEdge = memo(LabeledEdgeComponent)
