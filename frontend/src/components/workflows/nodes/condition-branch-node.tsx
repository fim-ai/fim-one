"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { GitBranch } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { ConditionNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function ConditionBranchNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as ConditionNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }
  const conditions = nodeData.conditions ?? []

  // Calculate vertical spacing for stacked source handles on the right
  const handleCount = conditions.length > 0 ? conditions.length : 1
  const handleSpacing = 100 / (handleCount + 1)

  return (
    <BaseWorkflowNode
      nodeType="conditionBranch"
      icon={<GitBranch className="h-3 w-3 text-orange-500" />}
      title={t("nodeType_conditionBranch")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {conditions.length > 0 && (
        <div className="space-y-0.5">
          {conditions.map((c, i) => (
            <p key={c.id ?? i} className="text-[10px] text-muted-foreground truncate">
              {c.label || `Condition ${i + 1}`}
            </p>
          ))}
        </div>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-orange-500 !border-orange-600/30 !-left-1"
      />
      {/* Source handles stacked vertically on the right */}
      {conditions.length > 0
        ? conditions.map((c, i) => (
            <Handle
              key={c.id ?? i}
              type="source"
              position={Position.Right}
              id={`condition-${c.id ?? i}`}
              className="!w-2 !h-2 !bg-orange-500 !border-orange-600/30 !-right-1"
              style={{ top: `${handleSpacing * (i + 1)}%` }}
            />
          ))
        : (
            <Handle
              type="source"
              position={Position.Right}
              id="source-default"
              className="!w-2 !h-2 !bg-orange-500 !border-orange-600/30 !-right-1"
            />
          )}
    </BaseWorkflowNode>
  )
}

export const ConditionBranchNode = memo(ConditionBranchNodeComponent)
