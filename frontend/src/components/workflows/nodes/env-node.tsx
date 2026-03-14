"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { KeyRound } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { ENVNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function ENVNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as ENVNodeData & { runStatus?: NodeRunStatus; _runOverlay?: NodeRunOverlayData }

  const keyCount = (nodeData.env_keys ?? []).length

  return (
    <BaseWorkflowNode
      nodeType="env"
      icon={<KeyRound className="h-3.5 w-3.5 text-amber-600" />}
      title={t("nodeType_env")}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="space-y-0.5">
        {keyCount > 0 && (
          <p className="text-[10px] text-muted-foreground truncate">
            {t("envKeyCount", { count: keyCount })}
          </p>
        )}
      </div>
      <Handle
        type="target"
        position={Position.Top}
        id="target"
        className="!w-2 !h-2 !bg-amber-600 !border-amber-600"
      />
      <Handle
        type="source"
        position={Position.Bottom}
        id="source"
        className="!w-2 !h-2 !bg-amber-600 !border-amber-600"
      />
    </BaseWorkflowNode>
  )
}

export const ENVNode = memo(ENVNodeComponent)
