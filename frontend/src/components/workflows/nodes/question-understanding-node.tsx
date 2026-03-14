"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { MessageCircleQuestion } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { QuestionUnderstandingNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function QuestionUnderstandingNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as QuestionUnderstandingNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }
  const mode = nodeData.mode ?? "rewrite"

  return (
    <BaseWorkflowNode
      nodeType="questionUnderstanding"
      icon={<MessageCircleQuestion className="h-3 w-3 text-pink-500" />}
      title={t("nodeType_questionUnderstanding")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="flex items-center gap-1">
        <span className="inline-flex items-center rounded bg-pink-500/10 px-1.5 py-0.5 text-[10px] font-medium text-pink-500 dark:text-pink-400">
          {t(`questionMode_${mode}` as Parameters<typeof t>[0])}
        </span>
      </div>
      {nodeData.input_variable && (
        <p className="text-[10px] text-muted-foreground truncate mt-0.5">
          {nodeData.input_variable}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-pink-500 !border-pink-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-pink-500 !border-pink-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const QuestionUnderstandingNode = memo(QuestionUnderstandingNodeComponent)
