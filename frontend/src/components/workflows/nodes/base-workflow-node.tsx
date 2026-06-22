"use client"

import { memo } from "react"
import { Loader2, Clock, AlertCircle, AlertTriangle, MessageSquare } from "lucide-react"
import { useTranslations } from "next-intl"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { NodeRunStatus, NodeRunOverlayData, NodeValidationState, WorkflowNodeType } from "@/types/workflow"

const categoryColorMap: Record<string, string> = {
  start: "bg-green-500",
  end: "bg-red-500",
  llm: "bg-blue-500",
  agent: "bg-indigo-500",
  knowledgeRetrieval: "bg-teal-500",
  conditionBranch: "bg-orange-500",
  connector: "bg-purple-500",
  humanIntervention: "bg-sky-500",
  mcp: "bg-violet-500",
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
  comment?: string
  selected?: boolean
  runStatus?: NodeRunStatus
  runOverlay?: NodeRunOverlayData
  validationState?: NodeValidationState
  children?: React.ReactNode
}

function BaseWorkflowNodeComponent({
  nodeType,
  icon,
  title,
  note,
  comment,
  selected,
  runStatus,
  runOverlay,
  validationState,
  children,
}: BaseWorkflowNodeProps) {
  const t = useTranslations("workflows")
  const barColor = categoryColorMap[nodeType] ?? "bg-muted"
  const statusStyle = runStatus ? runStatusStyles[runStatus] : null
  const showDot = runStatus && runStatus !== "pending"
  const showOverlay = runStatus && runStatus !== "pending" && runOverlay

  const errorCount = validationState?.errors.length ?? 0
  const warningCount = validationState?.warnings.length ?? 0
  const hasValidationIssues = errorCount > 0 || warningCount > 0
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
      {/* Validation badge in top-left corner */}
      {hasValidationIssues && (
        <ValidationBadge
          errorCount={errorCount}
          warningCount={warningCount}
          errors={validationState?.errors ?? []}
          warnings={validationState?.warnings ?? []}
        />
      )}

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

      {/* Comment indicator icon */}
      {comment && (
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className={cn(
                  "absolute top-1 right-1 z-10 flex items-center justify-center",
                  showDot && "top-3",
                )}
              >
                <MessageSquare className="h-3 w-3 text-muted-foreground/60" />
              </span>
            </TooltipTrigger>
            <TooltipContent
              side="top"
              sideOffset={4}
              className="max-w-[220px] text-left"
            >
              <p className="text-xs whitespace-pre-wrap break-words">{comment}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
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
              className="max-w-[340px] space-y-1.5 text-left"
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

function ValidationBadge({
  errorCount,
  warningCount,
  errors,
  warnings,
}: {
  errorCount: number
  warningCount: number
  errors: string[]
  warnings: string[]
}) {
  const t = useTranslations("workflows")

  // Error takes priority for the badge display
  const isError = errorCount > 0
  const badgeCount = isError ? errorCount : warningCount

  const tooltipContent = (
    <div className="max-w-[240px] space-y-1.5">
      <p className="font-medium text-[11px]">{t("validationIssues")}</p>
      {errors.map((msg, i) => (
        <div key={`err-${i}`} className="flex items-start gap-1.5">
          <AlertCircle className="h-3 w-3 shrink-0 mt-0.5 text-red-400" />
          <span className="text-[10px] leading-tight">{msg}</span>
        </div>
      ))}
      {warnings.map((msg, i) => (
        <div key={`warn-${i}`} className="flex items-start gap-1.5">
          <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5 text-amber-400" />
          <span className="text-[10px] leading-tight">{msg}</span>
        </div>
      ))}
    </div>
  )

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className={cn(
              "absolute -top-1.5 -left-1.5 z-10 flex items-center justify-center",
              "h-5 w-5 rounded-full text-[9px] font-bold leading-none cursor-default",
              isError
                ? "bg-destructive text-destructive-foreground"
                : "bg-amber-500/90 text-white",
            )}
          >
            {badgeCount}
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="p-2">
          {tooltipContent}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
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

  /** Try to pretty-print a JSON-ish string, truncated for tooltip display */
  const fmt = (raw: string) => {
    try {
      const parsed = JSON.parse(raw)
      const pretty = JSON.stringify(parsed, null, 2)
      // Truncate at ~300 chars for the tooltip
      return pretty.length > 300 ? pretty.slice(0, 300) + "\u2026" : pretty
    } catch {
      return raw
    }
  }

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
          <span className="text-background/70 ml-auto tabular-nums">
            {formatDuration(overlay.durationMs)}
          </span>
        )}
      </div>
      {overlay.inputPreview && (
        <div className="space-y-0.5">
          <span className="text-[10px] font-medium text-background/50 uppercase tracking-wide">{t("runOverlayInput")}</span>
          <pre className="text-[10px] font-mono whitespace-pre-wrap break-all bg-background/10 rounded px-1.5 py-1 max-h-[100px] overflow-auto leading-relaxed">
            {fmt(overlay.inputPreview)}
          </pre>
        </div>
      )}
      {overlay.outputPreview && (
        <div className="space-y-0.5">
          <span className="text-[10px] font-medium text-background/50 uppercase tracking-wide">{t("runOverlayOutput")}</span>
          <pre className="text-[10px] font-mono whitespace-pre-wrap break-all bg-background/10 rounded px-1.5 py-1 max-h-[100px] overflow-auto leading-relaxed">
            {fmt(overlay.outputPreview)}
          </pre>
        </div>
      )}
      {overlay.runError && (
        <div className="space-y-0.5">
          <span className="text-[10px] font-medium text-red-400 uppercase tracking-wide">{t("runOverlayError")}</span>
          <pre className="text-[10px] font-mono whitespace-pre-wrap break-all bg-red-500/10 rounded px-1.5 py-1 text-red-300">
            {overlay.runError}
          </pre>
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
