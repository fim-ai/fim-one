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
  ChevronRight,
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
  node_started: "text-blue-500",
  node_completed: "text-green-500",
  node_failed: "text-red-500",
  node_skipped: "text-zinc-400",
  node_retrying: "text-amber-500",
  run_completed: "text-green-500",
  run_failed: "text-red-500",
}

/** Small status icons for log event types */
const eventTypeIcons: Record<WorkflowLogEventType, React.ReactNode> = {
  node_started: <Loader2 className="h-3 w-3 animate-spin" />,
  node_completed: <CheckCircle2 className="h-3 w-3" />,
  node_failed: <XCircle className="h-3 w-3" />,
  node_skipped: <SkipForward className="h-3 w-3" />,
  node_retrying: <RotateCcw className="h-3 w-3" />,
  run_completed: <CheckCircle2 className="h-3 w-3" />,
  run_failed: <XCircle className="h-3 w-3" />,
}

/** Filter button colors (with background for active state) */
const filterBadgeClass: Record<string, string> = {
  node_completed: "bg-green-500/15 text-green-600 dark:text-green-400",
  node_failed: "bg-red-500/15 text-red-600 dark:text-red-400",
  node_skipped: "bg-zinc-500/15 text-zinc-500 dark:text-zinc-400",
  node_retrying: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
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

/** Try to pretty-print a value as indented JSON. Falls back to the raw string. */
function prettyJson(value: unknown): string {
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  return JSON.stringify(value, null, 2)
}

/**
 * Lightweight JSON syntax highlighter.
 * Tokenises a pretty-printed JSON string and wraps each token in a <span>
 * with Tailwind colour classes. No external dependency needed.
 */
function highlightJson(json: string): React.ReactNode[] {
  // Regex that matches JSON tokens: strings, numbers, booleans, null, and structural chars
  const tokenRegex =
    /("(?:[^"\\]|\\.)*")\s*:|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b|\b(true|false)\b|\b(null)\b|([{}[\],])/g

  const elements: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = tokenRegex.exec(json)) !== null) {
    // Push any whitespace/text between tokens as-is
    if (match.index > lastIndex) {
      elements.push(json.slice(lastIndex, match.index))
    }

    if (match[1] !== undefined) {
      // Key (captured with trailing colon removed from the match group)
      elements.push(
        <span key={match.index} className="text-blue-600 dark:text-blue-400">
          {match[1]}
        </span>,
      )
      // The colon and space that follow the key — push as plain text
      elements.push(": ")
    } else if (match[2] !== undefined) {
      // String value
      elements.push(
        <span key={match.index} className="text-green-600 dark:text-green-400">
          {match[2]}
        </span>,
      )
    } else if (match[3] !== undefined) {
      // Number
      elements.push(
        <span key={match.index} className="text-amber-600 dark:text-amber-400">
          {match[3]}
        </span>,
      )
    } else if (match[4] !== undefined) {
      // Boolean
      elements.push(
        <span key={match.index} className="text-purple-600 dark:text-purple-400">
          {match[4]}
        </span>,
      )
    } else if (match[5] !== undefined) {
      // null
      elements.push(
        <span key={match.index} className="text-purple-600 dark:text-purple-400">
          {match[5]}
        </span>,
      )
    } else if (match[6] !== undefined) {
      // Structural char: { } [ ] ,
      elements.push(
        <span key={match.index} className="text-zinc-500 dark:text-zinc-400">
          {match[6]}
        </span>,
      )
    }

    lastIndex = match.index + match[0].length
  }

  // Any remaining text after the last match
  if (lastIndex < json.length) {
    elements.push(json.slice(lastIndex))
  }

  return elements
}

/** Collapsible JSON viewer for node outputs — always collapsible */
function NodeOutputViewer({ output, expanded, onToggle }: { output: unknown; expanded: boolean; onToggle: () => void }) {
  const formatted = useMemo(() => prettyJson(output), [output])
  const highlighted = useMemo(() => highlightJson(formatted), [formatted])

  if (!expanded) return null

  return (
    <pre className="text-[11px] font-mono mt-1 whitespace-pre-wrap break-all p-1.5 rounded border border-border bg-muted/30 max-h-[160px] overflow-auto">
      {highlighted}
    </pre>
  )
}

/** Single node result card with collapsible output */
function NodeResultCard({
  nodeId,
  result,
  label,
  showId,
  t,
}: {
  nodeId: string
  result: NodeRunResult
  label: string
  showId: boolean
  t: ReturnType<typeof useTranslations<"workflows">>
}) {
  const [expanded, setExpanded] = useState(false)
  const hasOutput = result.output != null && result.status === "completed"

  return (
    <div className="rounded-md border border-border p-2">
      {/* Clickable header row — whole row toggles output */}
      <div
        className={cn("flex items-center gap-2", hasOutput && "cursor-pointer")}
        onClick={hasOutput ? () => setExpanded((v) => !v) : undefined}
      >
        {statusIcons[result.status]}
        <p className="text-xs font-medium text-foreground truncate">{label}</p>
        {showId && (
          <span className="text-[11px] text-muted-foreground shrink-0">({nodeId})</span>
        )}
        {result.duration_ms != null && (
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {fmtDuration(result.duration_ms / 1000)}
          </span>
        )}
        {hasOutput && (
          <span className="ml-auto text-muted-foreground shrink-0">
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform duration-200",
                !expanded && "-rotate-90",
              )}
            />
          </span>
        )}
      </div>
      {/* Non-clickable content below header */}
      <div className="ml-6">
        {result.status === "retrying" && result.retryAttempt != null && (
          <p className="text-[11px] text-amber-500 mt-0.5">
            {t("runPanelRetrying", {
              attempt: result.retryAttempt,
              max: result.maxRetries ?? "?",
            })}
          </p>
        )}
        {result.error && (
          <p className="text-[11px] text-destructive mt-0.5">{result.error}</p>
        )}
        {hasOutput && (
          <NodeOutputViewer output={result.output} expanded={expanded} onToggle={() => setExpanded((v) => !v)} />
        )}
      </div>
    </div>
  )
}

/** Final output section with JSON syntax highlighting */
function FinalOutputViewer({ finalOutputs }: { finalOutputs: Record<string, unknown> }) {
  const t = useTranslations("workflows")
  const formatted = useMemo(
    () => JSON.stringify(finalOutputs, null, 2),
    [finalOutputs],
  )
  const highlighted = useMemo(() => highlightJson(formatted), [formatted])

  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium">{t("runPanelOutput")}</p>
      <pre className="text-xs p-2 rounded-md border border-border bg-muted/50 font-mono overflow-auto max-h-[200px]">
        {highlighted}
      </pre>
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

/** Filterable event types — no separate "started" since we merge with completed */
const FILTERABLE_EVENTS: WorkflowLogEventType[] = [
  "node_completed",
  "node_failed",
  "node_skipped",
  "node_retrying",
]

/** A merged log row: started + completed/failed collapsed into one entry per node */
interface MergedLogEntry {
  /** Timestamp of the started event (or earliest event for this node) */
  timestamp: number
  nodeId: string | null
  /** Final event type for display badge (completed/failed/running) */
  eventType: WorkflowLogEventType
  /** Duration string if completed/failed */
  detail: string | null
  /** Input preview from either started or completed event */
  inputPreview: unknown
  /** Output preview from completed event */
  outputPreview: unknown
  /** Error message from failed event */
  error: string | null
  /** Whether this is a run-level event (run_completed/run_failed) — not a node */
  isRunEvent: boolean
  /** Original event (for run-level events or non-merged events) */
  originalEvent?: WorkflowLogEvent
}

/** Merge raw log events into per-node entries (started+completed → one row) */
function mergeLogEvents(events: WorkflowLogEvent[]): MergedLogEntry[] {
  const merged: MergedLogEntry[] = []
  // Track started nodes that haven't completed yet, keyed by nodeId
  // Use an array of indices to support multiple iterations of the same node type
  const pendingStarted = new Map<string, number>() // nodeId → index in merged[]

  for (const event of events) {
    const nodeId = event.nodeId

    if (event.eventType === "node_started" && nodeId) {
      // Create a pending "running" entry
      const idx = merged.length
      merged.push({
        timestamp: event.timestamp,
        nodeId,
        eventType: "node_started",
        detail: null,
        inputPreview: event.details.input_preview ?? null,
        outputPreview: null,
        error: null,
        isRunEvent: false,
      })
      pendingStarted.set(nodeId, idx)
    } else if ((event.eventType === "node_completed" || event.eventType === "node_failed") && nodeId) {
      const pendingIdx = pendingStarted.get(nodeId)
      if (pendingIdx != null) {
        // Merge into the existing started entry
        const entry = merged[pendingIdx]
        entry.eventType = event.eventType
        entry.detail = getEventDetail(event)
        entry.inputPreview = event.details.input_preview ?? entry.inputPreview
        entry.outputPreview = event.details.output_preview ?? null
        entry.error = (event.details.error as string) ?? null
        pendingStarted.delete(nodeId)
      } else {
        // No matching started — create standalone entry
        merged.push({
          timestamp: event.timestamp,
          nodeId,
          eventType: event.eventType,
          detail: getEventDetail(event),
          inputPreview: event.details.input_preview ?? null,
          outputPreview: event.details.output_preview ?? null,
          error: (event.details.error as string) ?? null,
          isRunEvent: false,
        })
      }
    } else if (event.eventType === "run_completed" || event.eventType === "run_failed") {
      merged.push({
        timestamp: event.timestamp,
        nodeId: null,
        eventType: event.eventType,
        detail: getEventDetail(event),
        inputPreview: null,
        outputPreview: null,
        error: (event.details.error as string) ?? null,
        isRunEvent: true,
        originalEvent: event,
      })
    } else {
      // retrying, skipped, etc. — standalone entry
      merged.push({
        timestamp: event.timestamp,
        nodeId,
        eventType: event.eventType,
        detail: getEventDetail(event),
        inputPreview: null,
        outputPreview: null,
        error: (event.details.error as string) ?? null,
        isRunEvent: false,
        originalEvent: event,
      })
    }
  }

  return merged
}

/** Single merged log entry — started+completed are shown as one row per node */
function MergedLogEntryRow({
  entry,
  nodeLabel,
}: {
  entry: MergedLogEntry
  nodeLabel: string | null
}) {
  const t = useTranslations("workflows")
  const [expanded, setExpanded] = useState(false)

  const hasDetails =
    (entry.eventType === "node_completed" || entry.eventType === "node_failed") &&
    (entry.inputPreview != null || entry.outputPreview != null)

  const inputJson = useMemo(() => {
    if (!hasDetails || entry.inputPreview == null) return null
    const raw = prettyJson(entry.inputPreview)
    return { raw, highlighted: highlightJson(raw) }
  }, [hasDetails, entry.inputPreview])

  const outputJson = useMemo(() => {
    if (!hasDetails || entry.outputPreview == null) return null
    const raw = prettyJson(entry.outputPreview)
    return { raw, highlighted: highlightJson(raw) }
  }, [hasDetails, entry.outputPreview])

  // Badge to show: for merged entries that are still "started" (running), show running style
  const badgeType = entry.eventType === "node_started" ? "node_started" : entry.eventType
  const badgeLabel = badgeType === "node_started"
    ? t("runPanelLogEvent_node_started" as Parameters<typeof t>[0])
    : t(`runPanelLogEvent_${badgeType}` as Parameters<typeof t>[0])

  return (
    <div className="py-0.5 px-1 rounded hover:bg-muted/50 transition-colors">
      <div
        className={cn("flex items-start gap-1.5 font-mono text-xs", hasDetails && "cursor-pointer")}
        onClick={hasDetails ? () => setExpanded((v) => !v) : undefined}
      >
        {/* Expand indicator */}
        {hasDetails ? (
          <span className="shrink-0 pt-px text-muted-foreground">
            {expanded ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </span>
        ) : (
          <span className="shrink-0 w-3" />
        )}

        {/* Timestamp */}
        <span className="text-[11px] text-muted-foreground tabular-nums shrink-0 pt-px">
          {fmtTimestamp(entry.timestamp)}
        </span>

        {/* Status icon */}
        <span className={cn("shrink-0 pt-px", eventBadgeClass[badgeType] ?? "text-zinc-500")}>
          {eventTypeIcons[badgeType] ?? eventTypeIcons.node_completed}
        </span>

        {/* Node label */}
        {nodeLabel && (
          <span className="text-[11px] text-foreground shrink-0 truncate max-w-[120px]" title={entry.nodeId ?? undefined}>
            {nodeLabel}
          </span>
        )}

        {/* Duration / detail */}
        {entry.detail && (
          <span className={cn(
            "text-[11px] truncate",
            entry.eventType === "node_failed" || entry.eventType === "run_failed"
              ? "text-destructive"
              : "text-muted-foreground",
          )}>
            {entry.detail}
          </span>
        )}
      </div>

      {/* Expanded I/O */}
      {expanded && hasDetails && (
        <div className="ml-5 mt-1 mb-1 space-y-1.5">
          {inputJson && (
            <div>
              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
                {t("runPanelLogInput")}
              </span>
              <pre className="text-[11px] font-mono mt-0.5 whitespace-pre-wrap break-all p-1.5 rounded border border-border bg-muted/30 max-h-[120px] overflow-auto">
                {inputJson.highlighted}
              </pre>
            </div>
          )}
          {outputJson && (
            <div>
              <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">
                {t("runPanelLogOutput")}
              </span>
              <pre className="text-[11px] font-mono mt-0.5 whitespace-pre-wrap break-all p-1.5 rounded border border-border bg-muted/30 max-h-[120px] overflow-auto">
                {outputJson.highlighted}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

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

  const mergedEntries = useMemo(() => {
    // Merge first, then filter by final status — so disabling "completed"
    // hides the entire merged entry, not just the completed half.
    const all = mergeLogEvents(logEvents)
    return all.filter((e) => {
      if (hiddenTypes.has(e.eventType)) return false
      // Also hide run-level events when their corresponding node filter is off
      if (e.eventType === "run_completed" && hiddenTypes.has("node_completed")) return false
      if (e.eventType === "run_failed" && hiddenTypes.has("node_failed")) return false
      return true
    })
  }, [logEvents, hiddenTypes])

  // Auto-scroll to bottom when new events arrive (unless user scrolled up)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    if (!userScrolledRef.current) {
      el.scrollTop = el.scrollHeight
    }
  }, [mergedEntries])

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
        <span className="text-[11px] text-muted-foreground mr-1">
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
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium border transition-colors",
                active
                  ? cn(filterBadgeClass[type], "border-transparent")
                  : "bg-transparent text-muted-foreground/50 border-border line-through",
              )}
            >
              {eventTypeIcons[type]}
              {t(`runPanelLogEvent_${type}` as Parameters<typeof t>[0])}
            </button>
          )
        })}
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="max-h-[50vh] overflow-y-auto rounded-md border border-border bg-muted/30"
      >
        {mergedEntries.length === 0 ? (
          <div className="p-4 text-center">
            <p className="text-xs text-muted-foreground">
              {logEvents.length === 0
                ? t("runPanelLogEmpty")
                : t("runPanelLogAllFiltered")}
            </p>
          </div>
        ) : (
          <div className="p-1.5 space-y-px">
            {mergedEntries.map((entry, idx) => (
              <MergedLogEntryRow
                key={idx}
                entry={entry}
                nodeLabel={entry.nodeId ? getNodeLabel(entry.nodeId) : null}
              />
            ))}
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
    <div className="flex flex-col h-full w-[420px] shrink-0 border-l border-border bg-background/95 backdrop-blur-sm overflow-hidden">
      {/* Row 1: Title + action buttons */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
        <h3 className="text-sm font-semibold text-foreground">
          {t("runPanelTitle")}
        </h3>
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
          <Button variant="ghost" size="icon-sm" onClick={onClose}>
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {/* Row 2: Status bar (only visible when running or has results) */}
      {(isRunning || hasResults) && (
        <div className="flex items-center gap-1.5 px-4 py-1.5 border-b border-border/20 bg-muted/30 shrink-0">
          {isRunning ? (
            <>
              <Loader2 className="h-3 w-3 text-blue-500 animate-spin shrink-0" />
              <span className="text-[11px] text-blue-600 dark:text-blue-400 font-medium">
                {t("runPanelStatusRunning")}
              </span>
            </>
          ) : finalError ? (
            <>
              <XCircle className="h-3 w-3 text-red-500 shrink-0" />
              <span className="text-[11px] text-red-600 dark:text-red-400 font-medium">
                {t("runPanelStatusFailed")}
              </span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
              <span className="text-[11px] text-green-600 dark:text-green-400 font-medium">
                {t("runPanelStatusCompleted")}
              </span>
            </>
          )}
          {totalNodeCount > 0 && (
            <>
              <span className="text-[11px] text-muted-foreground/50">·</span>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {t("runPanelProgress", {
                  completed: completedCount,
                  total: totalNodeCount,
                })}
              </span>
            </>
          )}
          {runDuration != null && (
            <>
              <span className="text-[11px] text-muted-foreground/50">·</span>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {fmtDuration(runDuration / 1000)}
              </span>
            </>
          )}
        </div>
      )}

      {/* Input form (no tabs) */}
      {showInputForm && (
        <div className="flex-1 overflow-y-auto">
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
                        <span className="text-[11px] text-muted-foreground ml-1.5">({v.type})</span>
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
        </div>
      )}

      {/* Tabbed results/logs view */}
      {showTabs && (
        <Tabs defaultValue="results" className="flex-1 flex flex-col min-h-0 gap-0">
          <div className="px-4 pt-2 shrink-0">
            <TabsList className="h-7">
              <TabsTrigger value="results" className="text-[11px] px-2.5 h-6">
                {t("runPanelTabResults")}
              </TabsTrigger>
              <TabsTrigger value="logs" className="text-[11px] px-2.5 h-6">
                {t("runPanelTabLogs")}
                {logEvents.length > 0 && (
                  <span className="ml-1 text-[10px] tabular-nums text-muted-foreground">
                    ({logEvents.length})
                  </span>
                )}
              </TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="results" className="flex-1 overflow-y-auto mt-0">
              <div className="p-4 space-y-4">
                {/* Loading state */}
                {isRunning && !hasResults && (
                  <div className="flex flex-col items-center justify-center py-12 gap-3">
                    <Loader2 className="h-6 w-6 text-blue-500 animate-spin" />
                    <p className="text-xs text-muted-foreground">{t("runPanelExecuting")}</p>
                  </div>
                )}
                {/* Running/Results */}
                {hasResults && (
                  <div className="space-y-2">
                    <p className="text-xs font-medium">{t("runPanelNodeResults")}</p>
                    {Object.entries(nodeResults).map(([nodeId, result]) => (
                      <NodeResultCard
                        key={nodeId}
                        nodeId={nodeId}
                        result={result}
                        label={getNodeLabel(nodeId)}
                        showId={!!nodeTypeMap[nodeId]}
                        t={t}
                      />
                    ))}
                  </div>
                )}

                {/* Final output */}
                {finalOutputs && (
                  <FinalOutputViewer finalOutputs={finalOutputs} />
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

          <TabsContent value="logs" className="flex-1 overflow-y-auto mt-0">
            <div className="p-4">
              <LogViewer logEvents={logEvents} nodeTypeMap={nodeTypeMap} />
            </div>
          </TabsContent>
        </Tabs>
      )}
    </div>
  )
}
