"use client"

import { useState, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Layers,
  Loader2,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Download,
  RotateCw,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Slider } from "@/components/ui/slider"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { workflowApi } from "@/lib/api"
import { toastWorkflowError } from "@/lib/workflow-errors"
import type { BatchRunResultItem, WorkflowBatchRunResponse } from "@/types/workflow"

interface BatchRunDialogProps {
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

type DialogPhase = "input" | "running" | "results"

function parseInputSets(raw: string): { inputs: Record<string, unknown>[]; error: string | null } {
  const trimmed = raw.trim()
  if (!trimmed) return { inputs: [], error: null }

  // Try parsing as a JSON array first
  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed)
      if (!Array.isArray(parsed)) {
        return { inputs: [], error: "Expected a JSON array" }
      }
      for (let i = 0; i < parsed.length; i++) {
        if (typeof parsed[i] !== "object" || parsed[i] === null || Array.isArray(parsed[i])) {
          return { inputs: [], error: `Item ${i + 1} is not a valid JSON object` }
        }
      }
      return { inputs: parsed as Record<string, unknown>[], error: null }
    } catch (e) {
      return { inputs: [], error: (e as Error).message }
    }
  }

  // Otherwise, parse line-by-line
  const lines = trimmed.split("\n").filter((l) => l.trim())
  const inputs: Record<string, unknown>[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim()
    if (!line) continue
    try {
      const parsed = JSON.parse(line)
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return { inputs: [], error: `Line ${i + 1}: not a valid JSON object` }
      }
      inputs.push(parsed as Record<string, unknown>)
    } catch (e) {
      return { inputs: [], error: `Line ${i + 1}: ${(e as Error).message}` }
    }
  }
  return { inputs, error: null }
}

export function BatchRunDialog({
  workflowId,
  open,
  onOpenChange,
}: BatchRunDialogProps) {
  const t = useTranslations("workflows")
  const tError = useTranslations("errors")
  const tc = useTranslations("common")

  const [phase, setPhase] = useState<DialogPhase>("input")
  const [inputText, setInputText] = useState("")
  const [maxParallel, setMaxParallel] = useState(3)
  const [parseError, setParseError] = useState<string | null>(null)
  const [parsedCount, setParsedCount] = useState(0)
  const [batchResponse, setBatchResponse] = useState<WorkflowBatchRunResponse | null>(null)
  const [expandedItems, setExpandedItems] = useState<Set<number>>(new Set())

  const handleInputChange = useCallback(
    (value: string) => {
      setInputText(value)
      const { inputs, error } = parseInputSets(value)
      setParseError(error)
      setParsedCount(error ? 0 : inputs.length)
    },
    [],
  )

  const handleStartBatch = useCallback(async () => {
    const { inputs, error } = parseInputSets(inputText)
    if (error || inputs.length === 0) {
      setParseError(error ?? t("batchRunNoInputs"))
      return
    }

    setPhase("running")
    try {
      const result = await workflowApi.batchRun(workflowId, inputs, maxParallel)
      setBatchResponse(result)
      setPhase("results")
    } catch (err) {
      toastWorkflowError(err, t("batchRunApiError"), tError, t)
      setPhase("input")
    }
  }, [inputText, maxParallel, workflowId, t, tError])

  const handleExportResults = useCallback(() => {
    if (!batchResponse) return
    const blob = new Blob([JSON.stringify(batchResponse, null, 2)], {
      type: "application/json",
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `batch-run-${batchResponse.batch_id}.json`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(t("batchRunExported"))
  }, [batchResponse, t])

  const handleReset = useCallback(() => {
    setPhase("input")
    setInputText("")
    setParseError(null)
    setParsedCount(0)
    setBatchResponse(null)
    setExpandedItems(new Set())
  }, [])

  const toggleExpanded = useCallback((index: number) => {
    setExpandedItems((prev) => {
      const next = new Set(prev)
      if (next.has(index)) {
        next.delete(index)
      } else {
        next.add(index)
      }
      return next
    })
  }, [])

  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen && phase === "running") return // prevent closing during run
      if (!nextOpen) {
        // Reset state when dialog closes
        setPhase("input")
        setInputText("")
        setParseError(null)
        setParsedCount(0)
        setBatchResponse(null)
        setExpandedItems(new Set())
      }
      onOpenChange(nextOpen)
    },
    [phase, onOpenChange],
  )

  const completedCount = batchResponse?.results.filter((r) => r.status === "completed").length ?? 0
  const failedCount = batchResponse?.results.filter((r) => r.status === "failed").length ?? 0

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-4 w-4" />
            {t("batchRunTitle")}
          </DialogTitle>
          <DialogDescription>{t("batchRunDescription")}</DialogDescription>
        </DialogHeader>

        {phase === "input" && (
          <div className="space-y-5 overflow-y-auto">
            {/* Input textarea */}
            <div className="space-y-1.5">
              <Label htmlFor="batch-input" className="text-sm font-medium">
                {t("batchRunInputLabel")}
              </Label>
              <Textarea
                id="batch-input"
                className="text-sm font-mono min-h-[160px] resize-none"
                value={inputText}
                onChange={(e) => handleInputChange(e.target.value)}
                placeholder={t("batchRunInputPlaceholder")}
                aria-invalid={!!parseError}
              />
              {parseError && (
                <p className="text-sm text-destructive">
                  {t("batchRunParseError", { error: parseError })}
                </p>
              )}
              {!parseError && parsedCount > 0 && (
                <p className="text-sm text-muted-foreground">
                  {t("batchRunInputCount", { count: parsedCount })}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                {t("batchRunInputHint")}
              </p>
            </div>

            {/* Parallelism slider */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-sm font-medium">
                  {t("batchRunParallelismLabel")}
                </Label>
                <span className="text-sm font-mono text-muted-foreground tabular-nums">
                  {maxParallel}
                </span>
              </div>
              <Slider
                value={[maxParallel]}
                onValueChange={(v) => setMaxParallel(v[0])}
                min={1}
                max={10}
                step={1}
              />
              <p className="text-xs text-muted-foreground">
                {t("batchRunParallelismHint")}
              </p>
            </div>
          </div>
        )}

        {phase === "running" && (
          <div className="flex flex-col items-center justify-center py-12 gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm font-medium">{t("batchRunRunning")}</p>
            <p className="text-xs text-muted-foreground">
              {t("batchRunInputCount", { count: parsedCount })}
            </p>
          </div>
        )}

        {phase === "results" && batchResponse && (
          <div className="space-y-4 overflow-y-auto flex-1 min-h-0">
            {/* Summary */}
            <div className="flex items-center gap-3 p-3 rounded-lg border border-border/60 bg-muted/30">
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                <span className="text-sm font-medium">
                  {t("batchRunCompleted", { count: completedCount })}
                </span>
              </div>
              {failedCount > 0 && (
                <div className="flex items-center gap-1.5">
                  <XCircle className="h-4 w-4 text-destructive" />
                  <span className="text-sm font-medium">
                    {t("batchRunFailed", { count: failedCount })}
                  </span>
                </div>
              )}
              <span className="text-sm text-muted-foreground ml-auto">
                {t("batchRunOfTotal", { total: batchResponse.total })}
              </span>
            </div>

            {/* Individual results */}
            <div className="space-y-1">
              {batchResponse.results.map((item, index) => (
                <ResultItem
                  key={item.run_id}
                  item={item}
                  index={index}
                  expanded={expandedItems.has(index)}
                  onToggle={() => toggleExpanded(index)}
                  t={t}
                />
              ))}
            </div>
          </div>
        )}

        <DialogFooter className="flex-row gap-2 sm:justify-between">
          <div>
            {phase === "results" && (
              <Button variant="ghost" size="sm" onClick={handleReset}>
                <RotateCw className="mr-1.5 h-3.5 w-3.5" />
                {t("batchRunReset")}
              </Button>
            )}
          </div>
          <div className="flex gap-2">
            {phase === "results" && (
              <Button variant="outline" size="sm" onClick={handleExportResults}>
                <Download className="mr-1.5 h-3.5 w-3.5" />
                {t("batchRunExportResults")}
              </Button>
            )}
            {phase === "input" && (
              <>
                <Button
                  variant="ghost"
                  className="px-6"
                  onClick={() => handleOpenChange(false)}
                >
                  {tc("cancel")}
                </Button>
                <Button
                  className="px-6"
                  onClick={handleStartBatch}
                  disabled={!!parseError || parsedCount === 0}
                >
                  {t("batchRunStartButton")}
                </Button>
              </>
            )}
            {phase === "results" && (
              <Button
                variant="ghost"
                className="px-6"
                onClick={() => handleOpenChange(false)}
              >
                {tc("close")}
              </Button>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResultItem({
  item,
  index,
  expanded,
  onToggle,
  t,
}: {
  item: BatchRunResultItem
  index: number
  expanded: boolean
  onToggle: () => void
  t: ReturnType<typeof useTranslations>
}) {
  const isCompleted = item.status === "completed"

  return (
    <div className="rounded-md border border-border/60">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/50 transition-colors"
        onClick={onToggle}
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="font-medium">{t("batchRunResultItem", { index: index + 1 })}</span>
        <Badge
          variant="secondary"
          className={
            isCompleted
              ? "text-[10px] px-1.5 py-0 h-5 bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/20"
              : "text-[10px] px-1.5 py-0 h-5 bg-destructive/15 text-destructive border-destructive/20"
          }
        >
          {isCompleted ? t("runStatus_completed") : t("runStatus_failed")}
        </Badge>
        {item.duration_ms != null && (
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">
            {item.duration_ms}ms
          </span>
        )}
      </button>

      {expanded && (
        <div className="border-t border-border/40 px-3 py-2 space-y-2">
          {/* Inputs */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">
              {t("batchRunResultInputs")}
            </p>
            <pre className="text-xs font-mono bg-muted/50 rounded p-2 overflow-x-auto max-h-32 whitespace-pre-wrap break-all">
              {JSON.stringify(item.inputs, null, 2)}
            </pre>
          </div>

          {/* Outputs or Error */}
          {isCompleted && item.outputs && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">
                {t("batchRunResultOutputs")}
              </p>
              <pre className="text-xs font-mono bg-muted/50 rounded p-2 overflow-x-auto max-h-32 whitespace-pre-wrap break-all">
                {JSON.stringify(item.outputs, null, 2)}
              </pre>
            </div>
          )}

          {item.error && (
            <div>
              <p className="text-xs font-medium text-destructive mb-1">
                {t("batchRunResultError")}
              </p>
              <pre className="text-xs font-mono bg-destructive/10 text-destructive rounded p-2 overflow-x-auto max-h-32 whitespace-pre-wrap break-all">
                {item.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
