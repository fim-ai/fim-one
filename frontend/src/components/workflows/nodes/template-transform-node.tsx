"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { FileText } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { TemplateTransformNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function TemplateTransformNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as TemplateTransformNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }

  return (
    <BaseWorkflowNode
      nodeType="templateTransform"
      icon={<FileText className="h-3 w-3 text-amber-500" />}
      title={t("nodeType_templateTransform")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {nodeData.template && (
        <p className="text-[10px] text-muted-foreground/70 line-clamp-1 font-mono">
          {nodeData.template}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-amber-500 !border-amber-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-amber-500 !border-amber-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const TemplateTransformNode = memo(TemplateTransformNodeComponent)
