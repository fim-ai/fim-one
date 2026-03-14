"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { MessageSquareMore } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { QuestionClassifierNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function QuestionClassifierNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as QuestionClassifierNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }
  const classes = nodeData.classes ?? []

  // Calculate vertical spacing for stacked source handles on the right
  const handleCount = classes.length > 0 ? classes.length : 1
  const handleSpacing = 100 / (handleCount + 1)

  return (
    <BaseWorkflowNode
      nodeType="questionClassifier"
      icon={<MessageSquareMore className="h-3 w-3 text-teal-500" />}
      title={t("nodeType_questionClassifier")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {classes.length > 0 && (
        <div className="space-y-0.5">
          {classes.map((c, i) => (
            <p key={c.id ?? i} className="text-[10px] text-muted-foreground truncate">
              {c.label || `Class ${i + 1}`}
            </p>
          ))}
        </div>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-teal-500 !border-teal-600/30 !-left-1"
      />
      {/* Source handles stacked vertically on the right */}
      {classes.length > 0
        ? classes.map((c, i) => (
            <Handle
              key={c.id ?? i}
              type="source"
              position={Position.Right}
              id={`class-${c.id ?? i}`}
              className="!w-2 !h-2 !bg-teal-500 !border-teal-600/30 !-right-1"
              style={{ top: `${handleSpacing * (i + 1)}%` }}
            />
          ))
        : (
            <Handle
              type="source"
              position={Position.Right}
              id="source-default"
              className="!w-2 !h-2 !bg-teal-500 !border-teal-600/30 !-right-1"
            />
          )}
    </BaseWorkflowNode>
  )
}

export const QuestionClassifierNode = memo(QuestionClassifierNodeComponent)
