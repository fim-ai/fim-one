"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Repeat } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { IteratorNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function IteratorNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as IteratorNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const maxIter = nodeData.max_iterations ?? 100

  return (
    <BaseWorkflowNode
      nodeType="iterator"
      icon={<Repeat className="h-3 w-3 text-cyan-500" />}
      title={t("nodeType_iterator")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground truncate">
        {nodeData.list_variable
          ? nodeData.list_variable
          : t("configListVariablePlaceholder")}
      </p>
      <p className="text-[10px] text-muted-foreground/60">
        max: {maxIter}
      </p>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-cyan-500 !border-cyan-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-cyan-500 !border-cyan-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const IteratorNode = memo(IteratorNodeComponent)
