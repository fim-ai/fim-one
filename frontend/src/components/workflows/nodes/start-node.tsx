"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Play } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { StartNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function StartNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as StartNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const varCount = nodeData.variables?.length ?? 0

  return (
    <BaseWorkflowNode
      nodeType="start"
      icon={<Play className="h-3 w-3 text-green-500" />}
      title={t("nodeType_start")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground">
        {t("variableCount", { count: varCount })}
      </p>
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-green-500 !border-green-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const StartNode = memo(StartNodeComponent)
