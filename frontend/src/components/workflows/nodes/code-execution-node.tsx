"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Code } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { CodeExecutionNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function CodeExecutionNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as CodeExecutionNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }

  return (
    <BaseWorkflowNode
      nodeType="codeExecution"
      icon={<Code className="h-3 w-3 text-emerald-500" />}
      title={t("nodeType_codeExecution")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {nodeData.language && (
        <span className="text-[10px] font-mono font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
          {nodeData.language}
        </span>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-emerald-500 !border-emerald-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-emerald-500 !border-emerald-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const CodeExecutionNode = memo(CodeExecutionNodeComponent)
