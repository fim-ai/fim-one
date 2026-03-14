"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { UserCheck } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { HumanInterventionNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function HumanInterventionNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as HumanInterventionNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }

  return (
    <BaseWorkflowNode
      nodeType="humanIntervention"
      icon={<UserCheck className="h-3 w-3 text-sky-500" />}
      title={t("nodeType_humanIntervention")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      {nodeData.prompt_message && (
        <p className="text-[10px] text-muted-foreground truncate max-w-[200px]">
          {nodeData.prompt_message}
        </p>
      )}
      {nodeData.assignee && (
        <span className="text-[10px] bg-sky-500/10 text-sky-600 dark:text-sky-400 px-1.5 py-0.5 rounded mt-1 inline-block">
          @{nodeData.assignee}
        </span>
      )}
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

export const HumanInterventionNode = memo(HumanInterventionNodeComponent)
