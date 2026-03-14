"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Combine } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { VariableAggregatorNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function VariableAggregatorNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as VariableAggregatorNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }
  const count = nodeData.variables?.length ?? 0
  const mode = nodeData.mode ?? "list"

  return (
    <BaseWorkflowNode
      nodeType="variableAggregator"
      icon={<Combine className="h-3 w-3 text-sky-500" />}
      title={t("nodeType_variableAggregator")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground">
        {t("variableCount", { count })} · {t(`configAggregateMode_${mode}` as Parameters<typeof t>[0])}
      </p>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-sky-500 !border-sky-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-sky-500 !border-sky-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const VariableAggregatorNode = memo(VariableAggregatorNodeComponent)
