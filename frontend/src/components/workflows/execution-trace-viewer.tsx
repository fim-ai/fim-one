"use client"

import { useState, useCallback, useMemo } from "react"
import { useTranslations } from "next-intl"
import { Collapsible as CollapsiblePrimitive } from "radix-ui"
import {
  Play,
  Square,
  Brain,
  GitBranch,
  MessageSquareMore,
  Bot,
  Library,
  Plug,
  Globe,
  Variable,
  FileText,
  Code,
  CheckCircle2,
  XCircle,
  SkipForward,
  ChevronRight,
  Copy,
  Check,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet"
import { cn } from "@/lib/utils"
import { fmtDuration } from "@/lib/utils"
import type { NodeRunResult, NodeRunStatus } from "@/types/workflow"

// ---------------------------------------------------------------------------
// Node type icon map (mirrors node-palette.tsx)
// ---------------------------------------------------------------------------

const nodeTypeIcons: Record<string, React.ReactNode> = {
  START: <Play className="h-3.5 w-3.5 text-green-500" />,
  END: <Square className="h-3.5 w-3.5 text-red-500" />,
  LLM: <Brain className="h-3.5 w-3.5 text-blue-500" />,
  CONDITION_BRANCH: <GitBranch className="h-3.5 w-3.5 text-orange-500" />,
  QUESTION_CLASSIFIER: <MessageSquareMore className="h-3.5 w-3.5 text-teal-500" />,
  AGENT: <Bot className="h-3.5 w-3.5 text-indigo-500" />,
  KNOWLEDGE_RETRIEVAL: <Library className="h-3.5 w-3.5 text-teal-500" />,
  CONNECTOR: <Plug className="h-3.5 w-3.5 text-purple-500" />,
  HTTP_REQUEST: <Globe className="h-3.5 w-3.5 text-slate-500" />,
  VARIABLE_ASSIGN: <Variable className="h-3.5 w-3.5 text-gray-500" />,
  TEMPLATE_TRANSFORM: <FileText className="h-3.5 w-3.5 text-amber-500" />,
  CODE_EXECUTION: <Code className="h-3.5 w-3.5 text-emerald-500" />,
}

function getNodeIcon(nodeType: string | undefined): React.ReactNode {
  if (!nodeType) return <Play className="h-3.5 w-3.5 text-muted-foreground" />
  return nodeTypeIcons[nodeType.toUpperCase()] ?? <Play className="h-3.5 w-3.5 text-muted-foreground" />
}

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const statusBadgeVariant: Record<NodeRunStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "outline",
  running: "secondary",
  completed: "secondary",
  failed: "destructive",
  skipped: "outline",
  retrying: "secondary",
}

const statusIcons: Record<NodeRunStatus, React.ReactNode> = {
  pending: null,
  running: null,
  completed: <CheckCircle2 className="h-3 w-3 text-green-500" />,
  failed: <XCircle className="h-3 w-3 text-red-500" />,
  skipped: <SkipForward className="h-3 w-3 text-zinc-400" />,
  retrying: null,
}

const timelineColors: Record<NodeRunStatus, string> = {
  pending: "bg-zinc-300 dark:bg-zinc-600",
  running: "bg-blue-500",
  completed: "bg-green-500",
  failed: "bg-red-500",
  skipped: "bg-zinc-300 dark:bg-zinc-600",
  retrying: "bg-amber-500",
}

// ---------------------------------------------------------------------------
// CopyButton
// ---------------------------------------------------------------------------

function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false)
  const t = useTranslations("workflows")

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [text])

  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-6 gap-1 text-[10px] px-1.5"
      onClick={handleCopy}
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" />
          {t("copied")}
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          {label}
        </>
      )}
    </Button>
  )
}

// ---------------------------------------------------------------------------
// TraceSection (collapsible)
// ---------------------------------------------------------------------------

function TraceSection({
  title,
  data,
  defaultOpen = false,
}: {
  title: string
  data: unknown
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const t = useTranslations("workflows")

  const jsonStr = useMemo(() => {
    if (data === null || data === undefined) return ""
    if (typeof data === "string") return data
    try {
      return JSON.stringify(data, null, 2)
    } catch {
      return String(data)
    }
  }, [data])

  if (!jsonStr) return null

  return (
    <CollapsiblePrimitive.Root open={open} onOpenChange={setOpen}>
      <CollapsiblePrimitive.Trigger asChild>
        <button
          className="flex w-full items-center gap-1.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring rounded-sm"
        >
          <ChevronRight
            className={cn(
              "h-3 w-3 transition-transform",
              open && "rotate-90",
            )}
          />
          {title}
        </button>
      </CollapsiblePrimitive.Trigger>
      <CollapsiblePrimitive.Content className="overflow-hidden data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0">
        <div className="relative group mt-1">
          <pre className="text-xs font-mono bg-muted rounded p-2 overflow-auto max-h-[200px] whitespace-pre-wrap break-all">
            {jsonStr}
          </pre>
          <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <CopyButton text={jsonStr} label={t("copyToClipboard")} />
          </div>
        </div>
      </CollapsiblePrimitive.Content>
    </CollapsiblePrimitive.Root>
  )
}

// ---------------------------------------------------------------------------
// NodeTraceEntry
// ---------------------------------------------------------------------------

function NodeTraceEntry({
  nodeId,
  result,
  isLast,
  autoOpen,
}: {
  nodeId: string
  result: NodeRunResult
  isLast: boolean
  autoOpen: boolean
}) {
  const t = useTranslations("workflows")
  const status = result.status as NodeRunStatus
  const nodeType = result.node_type
  const duration = result.duration_ms
  const hasTrace = !!result.trace
  const trace = result.trace

  return (
    <div className="flex gap-3">
      {/* Timeline line + dot */}
      <div className="flex flex-col items-center">
        <div
          className={cn(
            "h-3 w-3 rounded-full shrink-0 mt-1.5 border-2 border-background",
            timelineColors[status],
          )}
        />
        {!isLast && (
          <div
            className={cn(
              "w-0.5 flex-1 min-h-[16px]",
              timelineColors[status],
            )}
          />
        )}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pb-4">
        {/* Header */}
        <div className="flex items-center gap-2 flex-wrap">
          {getNodeIcon(nodeType)}
          <span className="text-xs font-medium text-foreground truncate max-w-[180px]">
            {nodeId}
          </span>
          <Badge variant={statusBadgeVariant[status]} className="text-[10px] px-1.5 py-0 h-4 gap-1">
            {statusIcons[status]}
            {t(`runStatus_${status}`)}
          </Badge>
          {duration != null && (
            <span className="text-[10px] text-muted-foreground tabular-nums ml-auto">
              {typeof duration === "number" && duration > 1000
                ? fmtDuration(duration / 1000)
                : `${duration}ms`}
            </span>
          )}
        </div>

        {/* Trace sections */}
        <div className="mt-2 space-y-1">
          {/* Output (always shown if present) */}
          {result.output != null && (
            <TraceSection
              title={t("traceOutput")}
              data={result.output}
              defaultOpen={autoOpen && status === "completed"}
            />
          )}

          {/* Error (auto-opened for failed nodes) */}
          {result.error && (
            <TraceSection
              title={t("traceError")}
              data={result.error}
              defaultOpen={autoOpen}
            />
          )}

          {/* Debug trace: Input snapshot */}
          {hasTrace && !!trace?.input_snapshot && (
            <TraceSection
              title={t("traceInput")}
              data={trace.input_snapshot}
            />
          )}

          {/* Debug trace: Variable snapshot */}
          {hasTrace && !!trace?.variable_snapshot && (
            <TraceSection
              title={t("traceVariables")}
              data={trace.variable_snapshot}
            />
          )}

          {/* Debug trace: Type-specific details */}
          {hasTrace && trace?.details != null && typeof trace.details === "object" && Object.keys(trace.details as Record<string, unknown>).length > 0 && (
            <TraceSection
              title={t("traceDetails")}
              data={trace.details}
            />
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ExecutionTraceViewer (Sheet)
// ---------------------------------------------------------------------------

interface ExecutionTraceViewerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  nodeResults: Record<string, NodeRunResult> | null
}

export function ExecutionTraceViewer({
  open,
  onOpenChange,
  nodeResults,
}: ExecutionTraceViewerProps) {
  const t = useTranslations("workflows")

  // Sort entries by execution order (started_at or just iteration order)
  const entries = useMemo(() => {
    if (!nodeResults) return []
    return Object.entries(nodeResults)
  }, [nodeResults])

  // Find the first failed node index for auto-opening
  const firstFailedIdx = useMemo(() => {
    return entries.findIndex(([, r]) => r.status === "failed")
  }, [entries])

  const hasAnyTrace = useMemo(() => {
    return entries.some(([, r]) => r.trace != null)
  }, [entries])

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full sm:max-w-lg p-0 flex flex-col">
        <SheetHeader className="px-6 pt-6 pb-3 border-b border-border/40 shrink-0">
          <SheetTitle className="text-sm">{t("executionTrace")}</SheetTitle>
          <SheetDescription className="text-xs">
            {hasAnyTrace
              ? t("debugTraceEnabled")
              : t("debugTraceDisabled")}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4">
            {entries.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-8">
                {t("noTraceData")}
              </p>
            ) : (
              <div>
                {entries.map(([nodeId, result], idx) => (
                  <NodeTraceEntry
                    key={nodeId}
                    nodeId={nodeId}
                    result={result}
                    isLast={idx === entries.length - 1}
                    autoOpen={idx === firstFailedIdx}
                  />
                ))}
              </div>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
