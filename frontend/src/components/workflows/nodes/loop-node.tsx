"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { RefreshCw } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { LoopNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function LoopNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as LoopNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const maxIter = nodeData.max_iterations ?? 50

  return (
    <BaseWorkflowNode
      nodeType="loop"
      icon={<RefreshCw className="h-3 w-3 text-orange-500" />}
      title={t("nodeType_loop")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground truncate font-mono">
        {nodeData.condition || t("configLoopCondition")}
      </p>
      <p className="text-[10px] text-muted-foreground/60">
        max: {maxIter}
      </p>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-orange-500 !border-orange-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-orange-500 !border-orange-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const LoopNode = memo(LoopNodeComponent)
