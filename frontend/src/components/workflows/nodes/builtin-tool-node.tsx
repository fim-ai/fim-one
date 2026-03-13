"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { useTranslations } from "next-intl"
import { Wrench } from "lucide-react"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { BuiltinToolNodeData, NodeRunStatus } from "@/types/workflow"

function BuiltinToolNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as BuiltinToolNodeData & { runStatus?: NodeRunStatus; note?: string }

  return (
    <BaseWorkflowNode
      nodeType="builtinTool"
      icon={<Wrench className="h-3 w-3 text-zinc-500" />}
      title={t("nodeType_builtinTool")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
    >
      <div className="space-y-0.5">
        {nodeData.tool_id && (
          <span className="text-[10px] bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 px-1.5 py-0.5 rounded inline-block">
            {nodeData.tool_id}
          </span>
        )}
      </div>
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-zinc-500 !border-zinc-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-zinc-500 !border-zinc-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const BuiltinToolNode = memo(BuiltinToolNodeComponent)
