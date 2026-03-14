"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Brain } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { LLMNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function LLMNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as LLMNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }

  return (
    <BaseWorkflowNode
      nodeType="llm"
      icon={<Brain className="h-3 w-3 text-blue-500" />}
      title={t("nodeType_llm")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {nodeData.model && (
        <p className="text-[10px] text-muted-foreground truncate">
          {nodeData.model}
        </p>
      )}
      {nodeData.prompt_template && (
        <p className="text-[10px] text-muted-foreground/70 line-clamp-1">
          {nodeData.prompt_template}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-blue-500 !border-blue-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-blue-500 !border-blue-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const LLMNode = memo(LLMNodeComponent)
