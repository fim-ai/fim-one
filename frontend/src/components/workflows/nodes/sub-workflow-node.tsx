"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { GitBranch } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { SubWorkflowNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function SubWorkflowNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as SubWorkflowNodeData & { runStatus?: NodeRunStatus; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }

  const mappingCount = Object.keys(nodeData.input_mapping ?? {}).length

  return (
    <BaseWorkflowNode
      nodeType="subWorkflow"
      icon={<GitBranch className="h-3.5 w-3.5 text-indigo-500" />}
      title={t("nodeType_subWorkflow")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="space-y-0.5">
        {nodeData.workflow_id && (
          <p className="text-[10px] text-muted-foreground truncate">
            {nodeData.workflow_id}
          </p>
        )}
        {mappingCount > 0 && (
          <p className="text-[10px] text-muted-foreground/70 truncate">
            {t("inputMappingCount", { count: mappingCount })}
          </p>
        )}
      </div>
      <Handle
        type="target"
        position={Position.Top}
        id="target"
        className="!w-2 !h-2 !bg-indigo-500 !border-indigo-500"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="source"
        className="!w-2 !h-2 !bg-indigo-500 !border-indigo-500"
      />
    </BaseWorkflowNode>
  )
}

export const SubWorkflowNode = memo(SubWorkflowNodeComponent)
