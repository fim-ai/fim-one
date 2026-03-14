"use client"

import { useState, useCallback } from "react"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import {
  Beaker,
  Loader2,
  CheckCircle2,
  XCircle,
  Plus,
  Trash2,
  ChevronDown,
  ChevronRight,
  Clock,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { workflowApi } from "@/lib/api"
import type { NodeTestResponse } from "@/types/workflow"
import type { WorkflowNodeType } from "@/types/workflow"

interface KeyValueRow {
  id: string
  key: string
  value: string
}

interface TestNodeDialogProps {
  workflowId: string
  nodeId: string
  nodeType: WorkflowNodeType
  nodeLabel: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function TestNodeDialog({
  workflowId,
  nodeId,
  nodeType,
  nodeLabel,
  open,
  onOpenChange,
}: TestNodeDialogProps) {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")

  const [variables, setVariables] = useState<KeyValueRow[]>([
    { id: crypto.randomUUID(), key: "", value: "" },
  ])
  const [envVars, setEnvVars] = useState<KeyValueRow[]>([])
  const [envExpanded, setEnvExpanded] = useState(false)
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState<NodeTestResponse | null>(null)
  const [varsAfterExpanded, setVarsAfterExpanded] = useState(false)

  // --- Variable rows ---
  const addVariable = useCallback(() => {
    setVariables((prev) => [...prev, { id: crypto.randomUUID(), key: "", value: "" }])
  }, [])

  const removeVariable = useCallback((id: string) => {
    setVariables((prev) => prev.filter((row) => row.id !== id))
  }, [])

  const updateVariable = useCallback((id: string, field: "key" | "value", val: string) => {
    setVariables((prev) =>
      prev.map((row) => (row.id === id ? { ...row, [field]: val } : row)),
    )
  }, [])

  // --- Env var rows ---
  const addEnvVar = useCallback(() => {
    setEnvVars((prev) => [...prev, { id: crypto.randomUUID(), key: "", value: "" }])
    setEnvExpanded(true)
  }, [])

  const removeEnvVar = useCallback((id: string) => {
    setEnvVars((prev) => prev.filter((row) => row.id !== id))
  }, [])

  const updateEnvVar = useCallback((id: string, field: "key" | "value", val: string) => {
    setEnvVars((prev) =>
      prev.map((row) => (row.id === id ? { ...row, [field]: val } : row)),
    )
  }, [])

  // --- Run test ---
  const handleRunTest = useCallback(async () => {
    // Build variables map from key-value rows (skip empty keys)
    const varsMap: Record<string, unknown> = {}
    for (const row of variables) {
      const k = row.key.trim()
      if (!k) continue
      // Try to parse as JSON; fall back to string
      try {
        varsMap[k] = JSON.parse(row.value)
      } catch {
        varsMap[k] = row.value
      }
    }

    // Build env vars map
    const envMap: Record<string, string> = {}
    for (const row of envVars) {
      const k = row.key.trim()
      if (!k) continue
      envMap[k] = row.value
    }

    setIsRunning(true)
    setResult(null)
    try {
      const response = await workflowApi.testNode(workflowId, {
        node_id: nodeId,
        variables: varsMap,
        env_vars: Object.keys(envMap).length > 0 ? envMap : undefined,
      })
      setResult(response)
      if (response.status === "completed") {
        toast.success(t("testNodeSuccess"))
      } else {
        toast.error(t("testNodeFailed"))
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      toast.error(msg)
    } finally {
      setIsRunning(false)
    }
  }, [workflowId, nodeId, variables, envVars, t])

  // Reset state when dialog closes
  const handleOpenChange = useCallback(
    (nextOpen: boolean) => {
      if (!nextOpen) {
        setResult(null)
        setIsRunning(false)
        setVarsAfterExpanded(false)
      }
      onOpenChange(nextOpen)
    },
    [onOpenChange],
  )

  return (
    <Sheet open={open} onOpenChange={handleOpenChange}>
      <SheetContent side="right" className="flex flex-col sm:max-w-md p-0">
        <SheetHeader className="px-6 pt-6 pb-4 border-b border-border/40 space-y-1">
          <div className="flex items-center gap-2">
            <Beaker className="h-4 w-4 text-muted-foreground shrink-0" />
            <SheetTitle className="text-base truncate">
              {t("testNodeTitle", { name: nodeLabel })}
            </SheetTitle>
          </div>
          <SheetDescription className="text-xs">
            {t(`nodeType_${nodeType}` as Parameters<typeof t>[0])}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4 space-y-5">
            {/* Input Variables */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-foreground">
                  {t("testNodeVariables")}
                </label>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs gap-1"
                  onClick={addVariable}
                >
                  <Plus className="h-3 w-3" />
                  {t("testNodeAddVariable")}
                </Button>
              </div>
              <div className="space-y-2">
                {variables.map((row) => (
                  <div key={row.id} className="flex items-center gap-1.5">
                    <Input
                      className="flex-1 h-7 text-xs"
                      placeholder="key"
                      value={row.key}
                      onChange={(e) => updateVariable(row.id, "key", e.target.value)}
                    />
                    <Input
                      className="flex-1 h-7 text-xs"
                      placeholder="value"
                      value={row.value}
                      onChange={(e) => updateVariable(row.id, "value", e.target.value)}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="h-7 w-7 shrink-0"
                      onClick={() => removeVariable(row.id)}
                    >
                      <Trash2 className="h-3 w-3 text-muted-foreground" />
                    </Button>
                  </div>
                ))}
              </div>
            </div>

            {/* Environment Variables (collapsible) */}
            <div className="space-y-2">
              <button
                type="button"
                className="flex items-center gap-1.5 text-xs font-medium text-foreground hover:text-foreground/80 transition-colors"
                onClick={() => setEnvExpanded((prev) => !prev)}
              >
                {envExpanded ? (
                  <ChevronDown className="h-3 w-3" />
                ) : (
                  <ChevronRight className="h-3 w-3" />
                )}
                {t("testNodeEnvVars")}
                {envVars.length > 0 && (
                  <Badge variant="secondary" className="ml-1 text-[10px] px-1.5 py-0">
                    {envVars.length}
                  </Badge>
                )}
              </button>
              {envExpanded && (
                <div className="space-y-2 pl-4">
                  {envVars.map((row) => (
                    <div key={row.id} className="flex items-center gap-1.5">
                      <Input
                        className="flex-1 h-7 text-xs"
                        placeholder="KEY"
                        value={row.key}
                        onChange={(e) => updateEnvVar(row.id, "key", e.target.value)}
                      />
                      <Input
                        className="flex-1 h-7 text-xs"
                        placeholder="value"
                        value={row.value}
                        onChange={(e) => updateEnvVar(row.id, "value", e.target.value)}
                      />
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        className="h-7 w-7 shrink-0"
                        onClick={() => removeEnvVar(row.id)}
                      >
                        <Trash2 className="h-3 w-3 text-muted-foreground" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs gap-1"
                    onClick={addEnvVar}
                  >
                    <Plus className="h-3 w-3" />
                    {t("testNodeAddVariable")}
                  </Button>
                </div>
              )}
            </div>

            <Separator />

            {/* Run Test Button */}
            <Button
              className="w-full gap-2"
              onClick={handleRunTest}
              disabled={isRunning}
            >
              {isRunning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("testNodeRunning")}
                </>
              ) : (
                <>
                  <Beaker className="h-4 w-4" />
                  {t("testNodeRun")}
                </>
              )}
            </Button>

            {/* Results */}
            {result && (
              <div className="space-y-4">
                <Separator />

                <div className="space-y-3">
                  <h4 className="text-xs font-medium text-foreground">
                    {t("testNodeResult")}
                  </h4>

                  {/* Status + Duration */}
                  <div className="flex items-center gap-3">
                    {result.status === "completed" ? (
                      <Badge variant="secondary" className="gap-1 text-xs bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20">
                        <CheckCircle2 className="h-3 w-3" />
                        {t("runStatus_completed")}
                      </Badge>
                    ) : (
                      <Badge variant="destructive" className="gap-1 text-xs">
                        <XCircle className="h-3 w-3" />
                        {t("runStatus_failed")}
                      </Badge>
                    )}
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      {result.duration_ms}ms
                    </span>
                  </div>

                  {/* Error */}
                  {result.error && (
                    <div className="space-y-1">
                      <label className="text-[11px] font-medium text-destructive">
                        {t("testNodeError")}
                      </label>
                      <pre className="text-xs text-destructive bg-destructive/5 border border-destructive/20 rounded-md p-3 whitespace-pre-wrap break-all overflow-auto max-h-[200px]">
                        {result.error}
                      </pre>
                    </div>
                  )}

                  {/* Output */}
                  <div className="space-y-1">
                    <label className="text-[11px] font-medium text-muted-foreground">
                      {t("testNodeOutput")}
                    </label>
                    <pre className="text-xs text-foreground bg-muted/50 border border-border/60 rounded-md p-3 whitespace-pre-wrap break-all overflow-auto max-h-[300px]">
                      {result.output != null
                        ? typeof result.output === "string"
                          ? result.output
                          : JSON.stringify(result.output, null, 2)
                        : "null"}
                    </pre>
                  </div>

                  {/* Variables After (collapsible) */}
                  {result.variables_after && Object.keys(result.variables_after).length > 0 && (
                    <div className="space-y-1">
                      <button
                        type="button"
                        className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground transition-colors"
                        onClick={() => setVarsAfterExpanded((prev) => !prev)}
                      >
                        {varsAfterExpanded ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        {t("testNodeVariablesAfter")}
                        <Badge variant="secondary" className="ml-1 text-[10px] px-1.5 py-0">
                          {Object.keys(result.variables_after).length}
                        </Badge>
                      </button>
                      {varsAfterExpanded && (
                        <pre className="text-xs text-foreground bg-muted/50 border border-border/60 rounded-md p-3 whitespace-pre-wrap break-all overflow-auto max-h-[300px]">
                          {JSON.stringify(result.variables_after, null, 2)}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
