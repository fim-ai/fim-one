"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { ArrowRightLeft } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { TransformNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function TransformNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as TransformNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const opCount = nodeData.operations?.length ?? 0

  return (
    <BaseWorkflowNode
      nodeType="transform"
      icon={<ArrowRightLeft className="h-3 w-3 text-rose-500" />}
      title={t("nodeType_transform")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground">
        {t("operationCount", { count: opCount })}
      </p>
      {nodeData.input_variable && (
        <p className="text-[10px] text-muted-foreground/60 truncate mt-0.5">
          {nodeData.input_variable}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-rose-500 !border-rose-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-rose-500 !border-rose-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const TransformNode = memo(TransformNodeComponent)
