"use client"

import { memo } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import {
  Loader2,
  CheckCircle2,
  CircleDashed,
  AlertCircle,
  Wrench,
} from "lucide-react"
import { cn, fmtDuration } from "@/lib/utils"
import type { StepNodeData } from "./types"

const statusConfig = {
  pending: {
    border: "border-zinc-500/40",
    glow: "",
    Icon: CircleDashed,
    iconClass: "text-zinc-500",
    badgeBg: "bg-zinc-500/10 text-zinc-400",
  },
  running: {
    border: "border-blue-500/60",
    glow: "shadow-[0_0_12px_rgba(59,130,246,0.25)]",
    Icon: Loader2,
    iconClass: "text-blue-500 animate-spin",
    badgeBg: "bg-blue-500/10 text-blue-400",
  },
  completed: {
    border: "border-green-500/50",
    glow: "",
    Icon: CheckCircle2,
    iconClass: "text-green-500",
    badgeBg: "bg-green-500/10 text-green-400",
  },
  failed: {
    border: "border-red-500/50",
    glow: "",
    Icon: AlertCircle,
    iconClass: "text-red-500",
    badgeBg: "bg-red-500/10 text-red-400",
  },
} as const

function StepNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as StepNodeData
  const config = statusConfig[nodeData.status]
  const { Icon } = config

  return (
    <div
      className={cn(
        "w-[200px] rounded-lg border bg-card/80 backdrop-blur-sm p-3 cursor-pointer transition-all duration-200 hover:brightness-110",
        config.border,
        config.glow
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        id="top"
        className="!w-2 !h-2 !bg-zinc-600 !border-zinc-500"
      />

      <div className="flex items-start gap-2 min-w-0">
        <Icon className={cn("h-4 w-4 shrink-0 mt-0.5", config.iconClass)} />
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "text-[10px] font-mono px-1.5 py-0.5 rounded",
                config.badgeBg
              )}
            >
              {nodeData.step_id}
            </span>
            {nodeData.duration != null && nodeData.status === "completed" && (
              <span className="text-[10px] text-muted-foreground ml-auto">
                {fmtDuration(nodeData.duration)}
              </span>
            )}
          </div>
          <p className="text-xs text-foreground/90 line-clamp-2 leading-relaxed">
            {nodeData.task}
          </p>
          {nodeData.tool_hint && (
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
              <Wrench className="h-2.5 w-2.5" />
              <span>{nodeData.tool_hint}</span>
            </div>
          )}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        id="bottom"
        className="!w-2 !h-2 !bg-zinc-600 !border-zinc-500"
      />
    </div>
  )
}

export const StepNode = memo(StepNodeComponent)
