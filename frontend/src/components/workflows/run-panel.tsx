"use client"

import { useState, useCallback, useMemo, useRef, useEffect } from "react"
import { useTranslations } from "next-intl"
import {
  CheckCircle2,
  XCircle,
  Loader2,
  X,
  Play,
  CircleDashed,
  SkipForward,
  ChevronDown,
  RotateCcw,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"
import { fmtDuration } from "@/lib/utils"
import type {
  StartNodeData,
  NodeRunResult,
  NodeRunStatus,
  WorkflowNodeType,
  WorkflowLogEvent,
  WorkflowLogEventType,
} from "@/types/workflow"

interface RunPanelProps {
  isOpen: boolean
  isRunning: boolean
  startVariables: StartNodeData["variables"]
  nodeResults: Record<string, NodeRunResult> | null
  finalOutputs: Record<string, unknown> | null
  finalError: string | null
  runDuration: number | null
  /** Map of nodeId -> node type for display labels */
  nodeTypeMap: Record<string, WorkflowNodeType>
  totalNodeCount: number
  logEvents: WorkflowLogEvent[]
  onStartRun: (inputs: Record<string, unknown>) => void
  onRunAgain: () => void
  onCancel: () => void
  onClose: () => void
}

const statusIcons: Record<NodeRunStatus, React.ReactNode> = {
  pending: <CircleDashed className="h-3.5 w-3.5 text-zinc-500" />,
  running: <Loader2 className="h-3.5 w-3.5 text-blue-500 animate-spin" />,
  completed: <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />,
  failed: <XCircle className="h-3.5 w-3.5 text-red-500" />,
  skipped: <SkipForward className="h-3.5 w-3.5 text-zinc-400" />,
  retrying: <RotateCcw className="h-3.5 w-3.5 text-amber-500 animate-spin" />,
}

/** Color class for each log event type badge */
const eventBadgeClass: Record<WorkflowLogEventType, string> = {
  node_started: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
  node_completed: "bg-green-500/15 text-green-600 dark:text-green-400",
  node_failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  node_skipped: "bg-zinc-500/15 text-zinc-500 dark:text-zinc-400",
  node_retrying: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  run_completed: "bg-green-500/15 text-green-600 dark:text-green-400",
  run_failed: "bg-red-500/15 text-red-600 dark:text-red-400",
}

/** Format a timestamp as HH:MM:SS.mmm */
function fmtTimestamp(ts: number): string {
  const d = new Date(ts)
  const h = String(d.getHours()).padStart(2, "0")
  const m = String(d.getMinutes()).padStart(2, "0")
  const s = String(d.getSeconds()).padStart(2, "0")
  const ms = String(d.getMilliseconds()).padStart(3, "0")
  return `${h}:${m}:${s}.${ms}`
}

/** Collapsible JSON viewer for node outputs */
function NodeOutputViewer({ output }: { output: unknown }) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  const formatted = useMemo(() => {
    if (typeof output === "string") return output
    return JSON.stringify(output, null, 2)
  }, [output])

  const isLong = formatted.length > 100 || formatted.includes("\n")

  if (!isLong) {
    return (
      <pre className="text-[10px] text-muted-foreground font-mono mt-0.5 whitespace-pre-wrap break-all">
        {formatted}
      </pre>
    )
  }

  return (
    <div className="mt-0.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-0.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronDown
          className={cn(
            "h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
        {expanded ? t("runPanelHideOutput") : t("runPanelShowOutput")}
      </button>
      {expanded ? (
        <pre className="text-[10px] text-muted-foreground font-mono mt-1 whitespace-pre-wrap break-all p-1.5 rounded border border-border bg-muted/30 max-h-[160px] overflow-auto">
          {formatted}
        </pre>
      ) : (
        <pre className="text-[10px] text-muted-foreground font-mono mt-0.5 line-clamp-2 whitespace-pre-wrap break-all">
          {formatted}
        </pre>
      )}
    </div>
  )
}

/** Extract a short detail string from a log event */
function getEventDetail(event: WorkflowLogEvent): string | null {
  const d = event.details
  if (event.eventType === "node_failed" || event.eventType === "run_failed") {
    return (d.error as string) ?? null
  }
  if (event.eventType === "node_retrying") {
    const attempt = d.attempt as number | undefined
    const max = d.max_retries as number | undefined
    if (attempt != null) return `attempt ${attempt}/${max ?? "?"}`
  }
  if (event.eventType === "node_completed" || event.eventType === "run_completed") {
    const dur = d.duration_ms as number | undefined
    if (dur != null) return `${fmtDuration(dur / 1000)}`
  }
  return null
}

/** All filterable event types */
const FILTERABLE_EVENTS: WorkflowLogEventType[] = [
  "node_started",
  "node_completed",
  "node_failed",
  "node_skipped",
  "node_retrying",
]

/** Execution log viewer component */
function LogViewer({
  logEvents,
  nodeTypeMap,
}: {
  logEvents: WorkflowLogEvent[]
  nodeTypeMap: Record<string, WorkflowNodeType>
}) {
  const t = useTranslations("workflows")
  const scrollRef = useRef<HTMLDivElement>(null)
  const [hiddenTypes, setHiddenTypes] = useState<Set<WorkflowLogEventType>>(new Set())
  // Track whether user has scrolled away from bottom
  const userScrolledRef = useRef(false)

  const toggleType = useCallback((type: WorkflowLogEventType) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev)
      if (next.has(type)) {
        next.delete(type)
      } else {
        next.add(type)
      }
      return next
    })
  }, [])

  const filteredEvents = useMemo(
    () => logEvents.filter((e) => !hiddenTypes.has(e.eventType)),
    [logEvents, hiddenTypes],
  )

  // Auto-scroll to bottom when new events arrive (unless user scrolled up)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (!userScrolledRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [filteredEvents])

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    // Consider "at bottom" if within 40px of the end
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    userScrolledRef.current = !atBottom
  }, [])

  /** Resolve a nodeId to a human-readable label */
  const getNodeLabel = (nodeId: string): string => {
    const nodeType = nodeTypeMap[nodeId]
    if (nodeType) {
      return t(`nodeType_${nodeType}` as Parameters<typeof t>[0])
    }
    return nodeId
  }

  return (
    <div className="space-y-2">
      {/* Filter toggles */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-[10px] text-muted-foreground mr-1">
          {t("runPanelLogFilter")}:
        </span>
        {FILTERABLE_EVENTS.map((type) => {
          const active = !hiddenTypes.has(type)
          return (
            <button
              key={type}
              type="button"
              onClick={() => toggleType(type)}
              className={cn(
                "inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium border transition-colors",
                active
                  ? cn(eventBadgeClass[type], "border-transparent")
                  : "bg-transparent text-muted-foreground/50 border-border line-through",
              )}
            >
              {t(`runPanelLogEvent_${type}` as Parameters<typeof t>[0])}
            </button>
          )
        })}
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="max-h-[220px] overflow-y-auto rounded-md border border-border bg-muted/30"
      >
        {filteredEvents.length === 0 ? (
          <div className="p-4 text-center">
            <p className="text-xs text-muted-foreground">
              {logEvents.length === 0
                ? t("runPanelLogEmpty")
                : t("runPanelLogAllFiltered")}
            </p>
          </div>
        ) : (
          <div className="p-1.5 space-y-px">
            {filteredEvents.map((event, idx) => {
              const detail = getEventDetail(event)
              return (
                <div
                  key={idx}
                  className="flex items-start gap-1.5 font-mono text-xs py-0.5 px-1 rounded hover:bg-muted/50 transition-colors"
                >
                  {/* Timestamp */}
                  <span className="text-[10px] text-muted-foreground tabular-nums shrink-0 pt-px">
                    {fmtTimestamp(event.timestamp)}
                  </span>

                  {/* Event type badge */}
                  <Badge
                    variant="secondary"
                    className={cn(
                      "text-[9px] px-1 py-0 rounded-sm font-semibold shrink-0 border-0",
                      eventBadgeClass[event.eventType],
                    )}
                  >
                    {t(`runPanelLogEvent_${event.eventType}` as Parameters<typeof t>[0])}
                  </Badge>

                  {/* Node ID */}
                  {event.nodeId && (
                    <span className="text-[10px] text-foreground shrink-0 truncate max-w-[120px]" title={event.nodeId}>
                      {getNodeLabel(event.nodeId)}
                    </span>
                  )}

                  {/* Detail */}
                  {detail && (
                    <span className={cn(
                      "text-[10px] truncate",
                      event.eventType === "node_failed" || event.eventType === "run_failed"
                        ? "text-destructive"
                        : "text-muted-foreground",
                    )}>
                      {detail}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export function RunPanel({
  isOpen,
  isRunning,
  startVariables,
  nodeResults,
  finalOutputs,
  finalError,
  runDuration,
  nodeTypeMap,
  totalNodeCount,
  logEvents,
  onStartRun,
  onRunAgain,
  onCancel,
  onClose,
}: RunPanelProps) {
  const t = useTranslations("workflows")
  const [inputValues, setInputValues] = useState<Record<string, string>>({})
  // Store the effective input strings from the last run so "Run Again" can pre-fill them
  const lastUsedInputsRef = useRef<Record<string, string>>({})

  const handleInputChange = useCallback(
    (name: string, value: string) => {
      setInputValues((prev) => ({ ...prev, [name]: value }))
    },
    [],
  )

  const handleRunAgainWithPrefill = useCallback(() => {
    // Pre-fill inputValues with the last-used effective values
    setInputValues({ ...lastUsedInputsRef.current })
    onRunAgain()
  }, [onRunAgain])

  const handleStartRun = useCallback(() => {
    const inputs: Record<string, unknown> = {}
    const effectiveStrings: Record<string, string> = {}
    for (const v of startVariables) {
      const raw = inputValues[v.name] ?? v.default_value ?? ""
      effectiveStrings[v.name] = raw
      if (v.type === "number") {
        inputs[v.name] = raw ? Number(raw) : 0
      } else if (v.type === "boolean") {
        inputs[v.name] = raw === "true"
      } else {
        inputs[v.name] = raw
      }
    }
    lastUsedInputsRef.current = effectiveStrings
    onStartRun(inputs)
  }, [startVariables, inputValues, onStartRun])

  if (!isOpen) return null

  const hasInputs = startVariables.length > 0
  const hasResults = nodeResults && Object.keys(nodeResults).length > 0
  const isFinished = !isRunning && hasResults
  const showInputForm = !isRunning && !hasResults
  // Show hint when inputs are pre-filled from a previous run
  const isPrefilled = showInputForm && hasInputs && Object.keys(lastUsedInputsRef.current).length > 0
    && startVariables.some((v) => inputValues[v.name] !== undefined && inputValues[v.name] !== "")

  // Progress calculation
  const completedCount = nodeResults
    ? Object.values(nodeResults).filter(
        (r) => r.status === "completed" || r.status === "failed" || r.status === "skipped",
      ).length
    : 0

  /** Resolve a nodeId to a human-readable label */
  const getNodeLabel = (nodeId: string): string => {
    const nodeType = nodeTypeMap[nodeId]
    if (nodeType) {
      const label = t(`nodeType_${nodeType}` as Parameters<typeof t>[0])
      // If there are multiple nodes of the same type, append a short suffix from the ID
      return label
    }
    return nodeId
  }

  // Show tabs only when there are results or running (i.e., not on the input form)
  const showTabs = isRunning || hasResults

  return (
    <div className="absolute bottom-0 left-0 right-0 z-10 border-t border-border bg-background/95 backdrop-blur-sm max-h-[50vh] overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border/40">
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-semibold text-foreground">
            {t("runPanelTitle")}
          </h3>
          {/* Progress indicator */}
          {(isRunning || hasResults) && totalNodeCount > 0 && (
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {t("runPanelProgress", {
                completed: completedCount,
                total: totalNodeCount,
              })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {isRunning && (
            <Button variant="outline" size="sm" className="h-6 text-xs gap-1" onClick={onCancel}>
              {t("runPanelCancel")}
            </Button>
          )}
          {isFinished && (
            <Button
              variant="outline"
              size="sm"
              className="h-6 text-xs gap-1"
              onClick={handleRunAgainWithPrefill}
            >
              <RotateCcw className="h-3 w-3" />
              {t("runPanelRunAgain")}
            </Button>
          )}
          {runDuration != null && (
            <span className="text-[10px] text-muted-foreground tabular-nums">
              {t("runPanelDuration")}: {fmtDuration(runDuration / 1000)}
            </span>
          )}
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Input form (no tabs) */}
      {showInputForm && (
        <ScrollArea className="max-h-[280px]">
          <div className="p-4 space-y-4">
            <div className="space-y-3">
              {hasInputs ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    {isPrefilled ? t("runPanelPrefilledFromLastRun") : t("runPanelProvideInputs")}
                  </p>
                  {startVariables.map((v) => (
                    <div key={v.name} className="space-y-1">
                      <label className="text-xs font-medium">
                        {v.name}
                        {v.required && <span className="text-destructive ml-0.5">*</span>}
                        <span className="text-[10px] text-muted-foreground ml-1.5">({v.type})</span>
                      </label>
                      <Input
                        className="h-7 text-xs"
                        placeholder={v.default_value ?? ""}
                        value={inputValues[v.name] ?? ""}
                        onChange={(e) => handleInputChange(v.name, e.target.value)}
                      />
                    </div>
                  ))}
                </>
              ) : (
                <p className="text-xs text-muted-foreground">{t("runPanelNoInputs")}</p>
              )}
              <Button size="sm" className="gap-1.5" onClick={handleStartRun}>
                <Play className="h-3.5 w-3.5" />
                {t("runPanelStartRun")}
              </Button>
            </div>
          </div>
        </ScrollArea>
      )}

      {/* Tabbed results/logs view */}
      {showTabs && (
        <Tabs defaultValue="results" className="gap-0">
          <div className="px-4 pt-2">
            <TabsList className="h-7">
              <TabsTrigger value="results" className="text-[11px] px-2.5 h-6">
                {t("runPanelTabResults")}
              </TabsTrigger>
              <TabsTrigger value="logs" className="text-[11px] px-2.5 h-6">
                {t("runPanelTabLogs")}
                {logEvents.length > 0 && (
                  <span className="ml-1 text-[9px] tabular-nums text-muted-foreground">
                    ({logEvents.length})
                  </span>
                )}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="results">
              <div className="p-4 space-y-4">
                {/* Running/Results */}
                {hasResults && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium">{t("runPanelNodeResults")}</p>
                    {Object.entries(nodeResults).map(([nodeId, result]) => (
                      <div
                        key={nodeId}
                        className="flex items-start gap-2 rounded-md border border-border p-2"
                      >
                        {statusIcons[result.status]}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5">
                            <p className="text-xs font-medium text-foreground truncate">
                              {getNodeLabel(nodeId)}
                            </p>
                            {nodeTypeMap[nodeId] && (
                              <span className="text-[10px] text-muted-foreground shrink-0">
                                ({nodeId})
                              </span>
                            )}
                          </div>
                          {result.duration_ms != null && (
                            <p className="text-[10px] text-muted-foreground tabular-nums">
                              {fmtDuration(result.duration_ms / 1000)}
                            </p>
                          )}
                          {result.status === "retrying" && result.retryAttempt != null && (
                            <p className="text-[10px] text-amber-500 mt-0.5">
                              {t("runPanelRetrying", {
                                attempt: result.retryAttempt,
                                max: result.maxRetries ?? "?",
                              })}
                            </p>
                          )}
                          {result.error && (
                            <p className="text-[10px] text-destructive mt-0.5">{result.error}</p>
                          )}
                          {result.output != null && result.status === "completed" && (
                            <NodeOutputViewer output={result.output} />
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Final output */}
                {finalOutputs && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium">{t("runPanelOutput")}</p>
                    <pre className={cn(
                      "text-xs p-2 rounded-md border border-border bg-muted/50 font-mono overflow-auto max-h-[120px]",
                    )}>
                      {JSON.stringify(finalOutputs, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Error */}
                {finalError && (
                  <div className="space-y-1.5">
                    <p className="text-xs font-medium text-destructive">{t("runPanelError")}</p>
                    <p className="text-xs text-destructive bg-destructive/10 p-2 rounded-md">
                      {finalError}
                    </p>
                  </div>
                )}
              </div>
          </TabsContent>

          <TabsContent value="logs">
            <div className="p-4">
              <LogViewer logEvents={logEvents} nodeTypeMap={nodeTypeMap} />
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
