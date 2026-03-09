"use client"

import { memo, useEffect, useState } from "react"
import { Handle, Position } from "@xyflow/react"
import type { NodeProps } from "@xyflow/react"
import {
  Loader2,
  CheckCircle2,
  CircleDashed,
  AlertCircle,
  Wrench,
  Clock,
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
    border: "border-amber-500/60",
    glow: "shadow-[0_0_12px_rgba(217,168,78,0.25)]",
    Icon: Loader2,
    iconClass: "text-amber-500 animate-spin",
    badgeBg: "bg-amber-500/10 text-amber-400",
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

/** Format a unix timestamp (seconds) into HH:MM:SS. */
function fmtTime(ts: number): string {
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString("en-US", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

/** Max number of tool badges visible before collapsing. */
const MAX_VISIBLE_TOOLS = 3

function ToolBadges({
  tools_used,
  tool_hint,
}: {
  tools_used?: string[]
  tool_hint?: string
}) {
  const hasTools = tools_used && tools_used.length > 0
  if (!hasTools && !tool_hint) return null

  if (!hasTools) {
    // Fallback: show tool_hint as a single badge
    return (
      <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
        <Wrench className="h-2.5 w-2.5 shrink-0" />
        <span className="truncate">{tool_hint}</span>
      </div>
    )
  }

  const visible = tools_used.slice(0, MAX_VISIBLE_TOOLS)
  const overflow = tools_used.length - MAX_VISIBLE_TOOLS

  return (
    <div className="flex flex-wrap items-center gap-1">
      {visible.map((name) => (
        <span
          key={name}
          className="bg-muted text-[10px] text-muted-foreground px-1.5 py-0.5 rounded truncate max-w-[80px]"
        >
          {name}
        </span>
      ))}
      {overflow > 0 && (
        <span className="text-[10px] text-muted-foreground/70">
          +{overflow}
        </span>
      )}
    </div>
  )
}

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() =>
    Math.max(0, Date.now() / 1000 - startedAt)
  )

  useEffect(() => {
    const id = setInterval(() => {
      setElapsed(Math.max(0, Date.now() / 1000 - startedAt))
    }, 100)
    return () => clearInterval(id)
  }, [startedAt])

  return (
    <span className="text-[10px] text-muted-foreground ml-auto tabular-nums">
      {fmtDuration(elapsed)}
    </span>
  )
}

function StepNodeComponent({ data }: NodeProps) {
  const nodeData = data as unknown as StepNodeData
  const config = statusConfig[nodeData.status]
  const { Icon } = config

  const showStartTime = nodeData.started_at != null
  const showCompletedDuration =
    nodeData.duration != null && nodeData.status === "completed"
  const showLiveTimer =
    nodeData.started_at != null && nodeData.status === "running"

  return (
    <div
      className={cn(
        "w-[200px] rounded-lg border bg-card p-3 transition-all duration-200 hover:brightness-[1.03] dark:hover:brightness-110",
        config.border,
        config.glow
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        id="left"
        className="!w-1.5 !h-1.5 !bg-zinc-300 !border-zinc-300 dark:!bg-zinc-600 dark:!border-zinc-500"
      />

      <div className="flex items-start gap-2 min-w-0">
        <Icon className={cn("h-4 w-4 shrink-0 mt-0.5", config.iconClass)} />
        <div className="flex-1 min-w-0 space-y-1">
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                "text-[10px] px-1.5 py-0.5 rounded",
                config.badgeBg
              )}
            >
              {nodeData.step_id}
            </span>
            {showCompletedDuration && (
              <span className="text-[10px] text-muted-foreground ml-auto tabular-nums">
                {fmtDuration(nodeData.duration!)}
              </span>
            )}
            {showLiveTimer && (
              <ElapsedTimer startedAt={nodeData.started_at!} />
            )}
          </div>
          <p className="text-xs text-foreground/90 line-clamp-2 leading-relaxed">
            {nodeData.task}
          </p>
          <ToolBadges
            tools_used={nodeData.tools_used}
            tool_hint={nodeData.tool_hint}
          />
          {showStartTime && (
            <div className="flex items-center gap-1 text-[10px] text-muted-foreground/70">
              <Clock className="h-2.5 w-2.5 shrink-0" />
              <span className="tabular-nums">{fmtTime(nodeData.started_at!)}</span>
            </div>
          )}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        id="right"
        className="!w-1.5 !h-1.5 !bg-zinc-300 !border-zinc-300 dark:!bg-zinc-600 dark:!border-zinc-500"
      />
    </div>
  )
}

export const StepNode = memo(StepNodeComponent)
