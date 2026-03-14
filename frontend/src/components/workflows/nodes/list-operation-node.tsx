"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { ListFilter } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { ListOperationNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function ListOperationNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as ListOperationNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const operation = nodeData.operation ?? "filter"

  return (
    <BaseWorkflowNode
      nodeType="listOperation"
      icon={<ListFilter className="h-3 w-3 text-lime-500" />}
      title={t("nodeType_listOperation")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="flex items-center gap-1">
        <span className="inline-flex items-center rounded bg-lime-500/10 px-1.5 py-0.5 text-[10px] font-medium text-lime-600 dark:text-lime-400">
          {operation}
        </span>
      </div>
      {nodeData.input_variable && (
        <p className="text-[10px] text-muted-foreground truncate mt-0.5">
          {nodeData.input_variable}
        </p>
      )}
      {nodeData.expression && operation !== "flatten" && operation !== "unique" && operation !== "reverse" && operation !== "length" && (
        <p className="text-[10px] text-muted-foreground/60 truncate mt-0.5">
          {nodeData.expression}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-lime-500 !border-lime-600/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-lime-500 !border-lime-600/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const ListOperationNode = memo(ListOperationNodeComponent)
