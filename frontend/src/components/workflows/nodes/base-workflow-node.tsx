"use client"

import { memo } from "react"
import { Loader2, Clock, AlertCircle } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { NodeRunStatus, NodeRunOverlayData, WorkflowNodeType } from "@/types/workflow"

const categoryColorMap: Record<string, string> = {
  start: "bg-green-500",
  end: "bg-red-500",
  llm: "bg-blue-500",
  questionClassifier: "bg-teal-500",
  agent: "bg-indigo-500",
  knowledgeRetrieval: "bg-teal-500",
  conditionBranch: "bg-orange-500",
  connector: "bg-purple-500",
  httpRequest: "bg-slate-500",
  variableAssign: "bg-gray-500",
  templateTransform: "bg-amber-500",
  codeExecution: "bg-emerald-500",
  iterator: "bg-cyan-500",
  loop: "bg-orange-500",
  variableAggregator: "bg-sky-500",
  parameterExtractor: "bg-violet-500",
  listOperation: "bg-lime-500",
  transform: "bg-rose-500",
  documentExtractor: "bg-amber-600",
  questionUnderstanding: "bg-pink-500",
  humanIntervention: "bg-sky-500",
  mcp: "bg-violet-500",
  builtinTool: "bg-zinc-500",
  subWorkflow: "bg-indigo-500",
  env: "bg-amber-600",
}

const runStatusStyles: Record<NodeRunStatus, { ring: string; extra: string }> = {
  pending: { ring: "", extra: "" },
  running: { ring: "ring-2 ring-blue-500/50", extra: "animate-pulse" },
  completed: { ring: "ring-2 ring-green-500/30", extra: "" },
  failed: { ring: "ring-2 ring-red-500/30", extra: "" },
  skipped: { ring: "", extra: "opacity-50" },
  retrying: { ring: "ring-2 ring-amber-500/50", extra: "animate-pulse" },
}

const statusDotColor: Record<NodeRunStatus, string> = {
  pending: "",
  running: "bg-blue-500",
  completed: "bg-green-500",
  failed: "bg-red-500",
  skipped: "bg-gray-400",
  retrying: "bg-amber-500",
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

interface BaseWorkflowNodeProps {
  nodeType: WorkflowNodeType
  icon: React.ReactNode
  title: string
  note?: string
  selected?: boolean
  runStatus?: NodeRunStatus
  runOverlay?: NodeRunOverlayData
  children?: React.ReactNode
}

function BaseWorkflowNodeComponent({
  nodeType,
  icon,
  title,
  note,
  selected,
  runStatus,
  runOverlay,
  children,
}: BaseWorkflowNodeProps) {
  const t = useTranslations("workflows")
  const barColor = categoryColorMap[nodeType] ?? "bg-muted"
  const statusStyle = runStatus ? runStatusStyles[runStatus] : null
  const showDot = runStatus && runStatus !== "pending"
  const showOverlay = runStatus && runStatus !== "pending" && runOverlay
  const hasTooltipContent =
    showOverlay &&
    (runOverlay.durationMs != null ||
      runOverlay.inputPreview ||
      runOverlay.outputPreview ||
      runOverlay.runError)

  const nodeCard = (
    <div
      className={cn(
        "relative w-[220px] rounded-md border bg-card shadow-sm transition-all duration-150 overflow-visible",
        statusStyle ? statusStyle.ring : "",
        statusStyle?.extra,
        !statusStyle && "border-border",
        selected && "outline-2 outline-offset-1 outline-primary",
      )}
    >
      {/* Status dot in top-right corner */}
      {showDot && (
        <>
          <span
            className={cn(
              "absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-card z-10",
              statusDotColor[runStatus],
            )}
          />
          {(runStatus === "running" || runStatus === "retrying") && (
            <span
              className={cn(
                "absolute -top-1 -right-1 h-2.5 w-2.5 rounded-full border-2 border-card animate-ping z-10",
                statusDotColor[runStatus],
              )}
            />
          )}
        </>
      )}

      <div className="flex flex-row">
        {/* Left color bar */}
        <div className={cn("w-1 shrink-0 rounded-l-md", barColor)} />

        {/* Content area */}
        <div className="flex-1 min-w-0">
          {/* Icon + title row */}
          <div className="flex items-center gap-1.5 px-2.5 pt-2 pb-1">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-muted/60">
              {icon}
            </div>
            <span className="text-[11px] font-medium text-card-foreground truncate flex-1">
              {title}
            </span>
            {runStatus && runStatus !== "pending" && (
              <RunStatusBadge status={runStatus} />
            )}
          </div>

          {/* Node-specific content */}
          {children && (
            <div className="px-2.5 pb-2 pt-0">
              {children}
            </div>
          )}
        </div>
      </div>

      {/* Execution overlay: duration badge, spinner, error indicator */}
      {showOverlay && (
        <div className="absolute -bottom-0.5 right-1 flex items-center gap-1 translate-y-full z-10">
          {runStatus === "running" && (
            <span className="flex items-center gap-0.5 text-[10px] bg-blue-500/10 text-blue-500 rounded px-1 py-px">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
            </span>
          )}
          {runStatus === "retrying" && (
            <span className="flex items-center gap-0.5 text-[10px] bg-amber-500/10 text-amber-500 rounded px-1 py-px">
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
            </span>
          )}
          {runStatus === "failed" && (
            <span className="flex items-center gap-0.5 text-[10px] bg-red-500/10 text-red-500 rounded px-1 py-px">
              <AlertCircle className="h-2.5 w-2.5" />
            </span>
          )}
          {runStatus === "completed" && runOverlay.durationMs != null && (
            <span className="flex items-center gap-0.5 text-[10px] bg-muted text-muted-foreground rounded px-1 py-px">
              <Clock className="h-2.5 w-2.5" />
              {formatDuration(runOverlay.durationMs)}
            </span>
          )}
        </div>
      )}
    </div>
  )

  return (
    <div className="flex flex-col items-start">
      {hasTooltipContent ? (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>
              {nodeCard}
            </TooltipTrigger>
            <TooltipContent
              side="bottom"
              sideOffset={8}
              className="max-w-[280px] space-y-1 text-left"
            >
              <RunTooltipContent
                status={runStatus}
                overlay={runOverlay}
                t={t}
              />
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        nodeCard
      )}

      {/* Note annotation below the node */}
      {note && (
        <p className="text-[10px] italic text-muted-foreground max-w-[220px] truncate mt-0.5 px-1">
          {note}
        </p>
      )}
    </div>
  )
}

function RunTooltipContent({
  status,
  overlay,
  t,
}: {
  status: NodeRunStatus
  overlay: NodeRunOverlayData
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const statusLabel = t(`runStatus_${status === "retrying" ? "running" : status}` as Parameters<typeof t>[0])

  return (
    <>
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full shrink-0",
            statusDotColor[status] || "bg-zinc-400",
          )}
        />
        <span className="font-medium">{statusLabel}</span>
        {overlay.durationMs != null && (
          <span className="text-background/70 ml-auto">
            {formatDuration(overlay.durationMs)}
          </span>
        )}
      </div>
      {overlay.inputPreview && (
        <div>
          <span className="text-background/60">{t("runOverlayInput")}:</span>{" "}
          <span className="break-all">{overlay.inputPreview}</span>
        </div>
      )}
      {overlay.outputPreview && (
        <div>
          <span className="text-background/60">{t("runOverlayOutput")}:</span>{" "}
          <span className="break-all">{overlay.outputPreview}</span>
        </div>
      )}
      {overlay.runError && (
        <div className="text-red-300">
          <span className="text-red-400">{t("runOverlayError")}:</span>{" "}
          <span className="break-all">{overlay.runError}</span>
        </div>
      )}
    </>
  )
}

function RunStatusBadge({ status }: { status: NodeRunStatus }) {
  const config: Record<NodeRunStatus, { bg: string; text: string; label: string }> = {
    pending: { bg: "bg-zinc-500/10", text: "text-zinc-500", label: "" },
    running: { bg: "bg-blue-500/10", text: "text-blue-500", label: "..." },
    completed: { bg: "bg-green-500/10", text: "text-green-500", label: "OK" },
    failed: { bg: "bg-red-500/10", text: "text-red-500", label: "ERR" },
    skipped: { bg: "bg-zinc-500/10", text: "text-zinc-500", label: "SKIP" },
    retrying: { bg: "bg-amber-500/10", text: "text-amber-500", label: "RETRY" },
  }
  const c = config[status]
  return (
    <span className={cn("text-[9px] font-mono px-1 py-0.5 rounded", c.bg, c.text)}>
      {c.label}
    </span>
  )
}

export const BaseWorkflowNode = memo(BaseWorkflowNodeComponent)
