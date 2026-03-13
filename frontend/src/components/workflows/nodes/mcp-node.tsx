"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { useTranslations } from "next-intl"
import { Cable } from "lucide-react"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { MCPNodeData, NodeRunStatus } from "@/types/workflow"

function MCPNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as MCPNodeData & { runStatus?: NodeRunStatus; note?: string }

  return (
    <BaseWorkflowNode
      nodeType="mcp"
      icon={<Cable className="h-3 w-3 text-violet-500" />}
      title={t("nodeType_mcp")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
    >
      <div className="space-y-0.5">
        {nodeData.server_id && (
          <p className="text-[10px] text-muted-foreground truncate">
            {nodeData.server_id}
          </p>
        )}
        {nodeData.tool_name && (
          <span className="text-[10px] bg-violet-500/10 text-violet-600 dark:text-violet-400 px-1.5 py-0.5 rounded inline-block">
            {nodeData.tool_name}
          </span>
        )}
      </div>
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

export const MCPNode = memo(MCPNodeComponent)
