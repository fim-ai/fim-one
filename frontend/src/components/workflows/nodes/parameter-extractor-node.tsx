"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { FileSearch } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { ParameterExtractorNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function ParameterExtractorNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as ParameterExtractorNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }
  const count = nodeData.parameters?.length ?? 0

  return (
    <BaseWorkflowNode
      nodeType="parameterExtractor"
      icon={<FileSearch className="h-3 w-3 text-violet-500" />}
      title={t("nodeType_parameterExtractor")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <p className="text-[10px] text-muted-foreground">
        {t("paramCount", { count })}
      </p>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-violet-500 !border-violet-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-violet-500 !border-violet-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const ParameterExtractorNode = memo(ParameterExtractorNodeComponent)
