"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import { FileScan } from "lucide-react"
import { useTranslations } from "next-intl"
import { BaseWorkflowNode } from "./base-workflow-node"
import type { DocumentExtractorNodeData, NodeRunStatus, NodeRunOverlayData } from "@/types/workflow"

function DocumentExtractorNodeComponent({ data, selected }: NodeProps) {
  const t = useTranslations("workflows")
  const nodeData = data as unknown as DocumentExtractorNodeData & { runStatus?: NodeRunStatus; note?: string; _runOverlay?: NodeRunOverlayData }
  const extractMode = nodeData.extract_mode ?? "full_text"
  const inputType = nodeData.input_type ?? "text"

  return (
    <BaseWorkflowNode
      nodeType="documentExtractor"
      icon={<FileScan className="h-3 w-3 text-amber-600" />}
      title={t("nodeType_documentExtractor")}
      note={nodeData.note}
      selected={selected}
      runStatus={nodeData.runStatus}
      runOverlay={nodeData._runOverlay}
    >
      <div className="flex items-center gap-1">
        <span className="inline-flex items-center rounded bg-amber-600/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-600 dark:text-amber-400">
          {t(`extractMode_${extractMode}` as Parameters<typeof t>[0])}
        </span>
        <span className="inline-flex items-center rounded bg-amber-600/5 px-1 py-0.5 text-[9px] text-amber-600/70 dark:text-amber-400/70">
          {t(`inputType_${inputType}` as Parameters<typeof t>[0])}
        </span>
      </div>
      {nodeData.input_variable && (
        <p className="text-[10px] text-muted-foreground truncate mt-0.5">
          {nodeData.input_variable}
        </p>
      )}
      <Handle
        type="target"
        position={Position.Left}
        id="target"
        className="!w-2 !h-2 !bg-amber-600 !border-amber-700/30 !-left-1"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="source"
        className="!w-2 !h-2 !bg-amber-600 !border-amber-700/30 !-right-1"
      />
    </BaseWorkflowNode>
  )
}

export const DocumentExtractorNode = memo(DocumentExtractorNodeComponent)
