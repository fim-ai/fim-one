"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { Plug } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { ConnectorNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function ConnectorNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as ConnectorNodeData & { runStatus?: NodeRunStatus; connector_name?: string; note?: string; comment?: string; _runOverlay?: NodeRunOverlayData }

  return (
    <BaseWorkflowNode
      nodeType="connector"
      icon={<Plug className="h-3 w-3 text-purple-500" />}
      title={t("nodeType_connector")}
      note={nodeData.note}
      comment={nodeData.comment}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="space-y-0.5">
        {nodeData.connector_name && (
          <p className="text-[10px] text-muted-foreground truncate">
            {nodeData.connector_name}
          </p>
        )}
        {nodeData.action && (
          <p className="text-[10px] text-muted-foreground/70 truncate">
            {nodeData.action}
          </p>
        )}
      </div>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-purple-500 !border-purple-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-purple-500 !border-purple-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const ConnectorNode = memo(ConnectorNodeComponent)
