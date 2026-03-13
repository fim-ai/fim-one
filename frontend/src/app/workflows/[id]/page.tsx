"use client"

import { useState, useEffect, useCallback, useRef, useMemo } from "react"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { toast } from "sonner"
import { Loader2, Clock } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/contexts/auth-context"
import { workflowApi, orgApi } from "@/lib/api"
import type { UserOrg } from "@/lib/api"
import { getApiBaseUrl, ACCESS_TOKEN_KEY } from "@/lib/constants"
import { WorkflowToolbar, type ValidationResult } from "@/components/workflows/workflow-toolbar"
import { WorkflowEditor } from "@/components/workflows/workflow-editor"
import type { WorkflowEditorHandle } from "@/components/workflows/workflow-editor"
import { RunHistorySheet } from "@/components/workflows/run-history-sheet"
import { VersionHistorySheet } from "@/components/workflows/version-history-sheet"
import { WorkflowStatsPanel } from "@/components/workflows/workflow-stats-panel"
import { NodeStatsPanel } from "@/components/workflows/node-stats-panel"
import { ValidationPanel } from "@/components/workflows/validation-panel"
import { EnvVarsDialog } from "@/components/workflows/env-vars-dialog"
import { VariablesPanel } from "@/components/workflows/variables-panel"
import { AnalyticsPanel } from "@/components/workflows/analytics-panel"
import type {
  WorkflowResponse,
  WorkflowBlueprint,
  WorkflowVariable,
  WorkflowNodeType,
  WorkflowValidateResponse,
  StartNodeData,
  NodeRunResult,
} from "@/types/workflow"

export default function WorkflowEditorPage() {
  const t = useTranslations("workflows")
  const to = useTranslations("organizations")
  const tc = useTranslations("common")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const params = useParams()
  const searchParams = useSearchParams()
  const workflowId = params.id as string

  const [workflow, setWorkflow] = useState<WorkflowResponse | null>(null)
  const [isLoadingWorkflow, setIsLoadingWorkflow] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isDuplicating, setIsDuplicating] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null)
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false)
  const [showPublishDialog, setShowPublishDialog] = useState(false)
  const [showUnpublishDialog, setShowUnpublishDialog] = useState(false)
  const [publishOrgId, setPublishOrgId] = useState<string>("")
  const [userOrgs, setUserOrgs] = useState<UserOrg[]>([])
  const [orgsLoading, setOrgsLoading] = useState(false)
  const pendingNavigationRef = useRef<string | null>(null)
  const editorRef = useRef<WorkflowEditorHandle>(null)

  // Run state
  const [isRunning, setIsRunning] = useState(false)
  const [runPanelOpen, setRunPanelOpen] = useState(false)
  const [nodeResults, setNodeResults] = useState<Record<string, NodeRunResult> | null>(null)
  const [finalOutputs, setFinalOutputs] = useState<Record<string, unknown> | null>(null)
  const [finalError, setFinalError] = useState<string | null>(null)
  const [runDuration, setRunDuration] = useState<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // History sheet state
  const [historyOpen, setHistoryOpen] = useState(false)

  // Version history sheet state
  const [versionHistoryOpen, setVersionHistoryOpen] = useState(false)

  // Stats panel state
  const [statsOpen, setStatsOpen] = useState(false)
  const [nodeStatsOpen, setNodeStatsOpen] = useState(false)

  // Env vars dialog state
  const [showEnvDialog, setShowEnvDialog] = useState(false)

  // Variables panel state
  const [variablesPanelOpen, setVariablesPanelOpen] = useState(false)

  // Analytics panel state
  const [analyticsOpen, setAnalyticsOpen] = useState(false)

  // Undo/redo state (synced from editor via callback)
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  // Validation state (client-side, debounced)
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null)
  const [isValidating, setIsValidating] = useState(false)
  const validationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Server-side validation panel state
  const [serverValidationOpen, setServerValidationOpen] = useState(false)
  const [serverValidationResult, setServerValidationResult] = useState<WorkflowValidateResponse | null>(null)
  const [isServerValidating, setIsServerValidating] = useState(false)

  const handleUndoRedoChange = useCallback((newCanUndo: boolean, newCanRedo: boolean) => {
    setCanUndo(newCanUndo)
    setCanRedo(newCanRedo)
  }, [])

  const handleUndo = useCallback(() => {
    editorRef.current?.undo()
  }, [])

  const handleRedo = useCallback(() => {
    editorRef.current?.redo()
  }, [])

  // Blueprint state managed by editor
  const blueprintRef = useRef<WorkflowBlueprint>({
    nodes: [],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
  })

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // Debounced blueprint validation
  const triggerValidation = useCallback(() => {
    if (validationTimerRef.current) {
      clearTimeout(validationTimerRef.current)
    }
    validationTimerRef.current = setTimeout(async () => {
      const bp = blueprintRef.current
      // Skip validation for empty blueprints
      if (bp.nodes.length === 0) {
        setValidationResult(null)
        return
      }
      setIsValidating(true)
      try {
        const result = await workflowApi.validate(bp as unknown as Record<string, unknown>)
        setValidationResult(result)
      } catch {
        // Silently ignore validation failures — don't block the user
        setValidationResult(null)
      } finally {
        setIsValidating(false)
      }
    }, 2000)
  }, [])

  // Clean up timers on unmount
  useEffect(() => {
    return () => {
      if (validationTimerRef.current) {
        clearTimeout(validationTimerRef.current)
      }
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
    }
  }, [])

  // Load workflow
  useEffect(() => {
    if (!user || !workflowId) return
    let cancelled = false
    setIsLoadingWorkflow(true)
    workflowApi
      .get(workflowId)
      .then((data) => {
        if (cancelled) return
        setWorkflow(data)
        blueprintRef.current = data.blueprint ?? {
          nodes: [],
          edges: [],
          viewport: { x: 0, y: 0, zoom: 1 },
        }
        // Trigger initial validation after load
        triggerValidation()
      })
      .catch(() => {
        if (!cancelled) {
          toast.error(t("workflowUpdateFailed"))
          router.replace("/workflows")
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingWorkflow(false)
      })
    return () => {
      cancelled = true
    }
  }, [user, workflowId, router, t, triggerValidation])

  // Dirty state beforeunload guard
  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [isDirty])

  const handleNameChange = useCallback(
    async (name: string) => {
      if (!workflow) return
      setWorkflow((prev) => prev ? { ...prev, name } : prev)
      setIsDirty(true)
    },
    [workflow],
  )

  const handleDescriptionChange = useCallback(
    (description: string) => {
      if (!workflow) return
      setWorkflow((prev) => prev ? { ...prev, description } : prev)
      setIsDirty(true)
    },
    [workflow],
  )

  const handleSave = useCallback(async () => {
    if (!workflow) return
    setIsSaving(true)
    try {
      // Extract input_schema from start node
      const startNode = blueprintRef.current.nodes.find((n) => n.type === "start")
      const startData = startNode?.data as StartNodeData | undefined
      const inputSchema: Record<string, unknown> = {}
      if (startData?.variables) {
        for (const v of startData.variables) {
          inputSchema[v.name] = { type: v.type, required: v.required ?? false }
        }
      }

      // Extract output_schema from end node
      const endNode = blueprintRef.current.nodes.find((n) => n.type === "end")
      const endData = endNode?.data as { output_mapping?: Record<string, string> } | undefined
      const outputSchema = endData?.output_mapping
        ? Object.fromEntries(Object.keys(endData.output_mapping).map((k) => [k, { type: "string" }]))
        : null

      const updated = await workflowApi.update(workflow.id, {
        name: workflow.name,
        description: workflow.description,
        icon: workflow.icon,
        blueprint: blueprintRef.current,
        input_schema: Object.keys(inputSchema).length > 0 ? inputSchema : null,
        output_schema: outputSchema,
      })
      setWorkflow(updated)
      setIsDirty(false)
      setLastSavedAt(new Date())
    } catch {
      toast.error(t("workflowUpdateFailed"))
    } finally {
      setIsSaving(false)
    }
  }, [workflow, t])

  const handleBlueprintChange = useCallback((bp: WorkflowBlueprint) => {
    blueprintRef.current = bp
    setIsDirty(true)
    triggerValidation()

    // Auto-save after 5 seconds of inactivity
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
    }
    autoSaveTimerRef.current = setTimeout(() => {
      handleSave()
    }, 5000)
  }, [triggerValidation, handleSave])

  const handleVariablesChange = useCallback(
    (variables: WorkflowVariable[]) => {
      const updated = { ...blueprintRef.current, variables }
      handleBlueprintChange(updated)
    },
    [handleBlueprintChange],
  )

  // Keyboard shortcuts (Cmd+S to save immediately, cancels pending auto-save)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault()
        if (!isSaving) {
          if (autoSaveTimerRef.current) {
            clearTimeout(autoSaveTimerRef.current)
            autoSaveTimerRef.current = null
          }
          handleSave()
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [isSaving, handleSave])

  const handleRun = useCallback(() => {
    setRunPanelOpen(true)
    setNodeResults(null)
    setFinalOutputs(null)
    setFinalError(null)
    setRunDuration(null)
  }, [])

  // Auto-open run panel when navigated with ?run=true
  useEffect(() => {
    if (searchParams.get("run") === "true" && workflow && !isLoadingWorkflow) {
      handleRun()
      router.replace(`/workflows/${workflowId}`, { scroll: false })
    }
  }, [searchParams, workflow, isLoadingWorkflow, workflowId, router, handleRun])

  const handleRunAgain = useCallback(() => {
    setNodeResults(null)
    setFinalOutputs(null)
    setFinalError(null)
    setRunDuration(null)
  }, [])

  const handleStartRun = useCallback(
    async (inputs: Record<string, unknown>) => {
      if (!workflow) return

      // Abort any existing run
      if (abortRef.current) {
        abortRef.current.abort()
      }

      const controller = new AbortController()
      abortRef.current = controller
      setIsRunning(true)
      setNodeResults({})
      setFinalOutputs(null)
      setFinalError(null)
      setRunDuration(null)

      try {
        const token = typeof window !== "undefined" ? localStorage.getItem(ACCESS_TOKEN_KEY) : null
        const headers: Record<string, string> = {
          "Content-Type": "application/json",
        }
        if (token) headers["Authorization"] = `Bearer ${token}`

        const res = await fetch(
          `${getApiBaseUrl()}/api/workflows/${workflow.id}/run`,
          {
            method: "POST",
            headers,
            body: JSON.stringify({ inputs }),
            signal: controller.signal,
          },
        )

        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail || `HTTP ${res.status}`)
        }

        if (!res.body) throw new Error("No response body")

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ""
        let currentEvent = "message"
        let currentData = ""

        const dispatch = (eventType: string, rawData: string) => {
          try {
            const data = JSON.parse(rawData) as Record<string, unknown>

            if (
              eventType === "node_started" ||
              eventType === "node_completed" ||
              eventType === "node_failed" ||
              eventType === "node_skipped" ||
              eventType === "node_retrying"
            ) {
              const nodeId = data.node_id as string
              const status: NodeRunResult["status"] =
                eventType === "node_started" ? "running"
                : eventType === "node_completed" ? "completed"
                : eventType === "node_failed" ? "failed"
                : eventType === "node_retrying" ? "retrying"
                : "skipped"
              setNodeResults((prev) => ({
                ...(prev ?? {}),
                [nodeId]: {
                  status,
                  output: data.output_preview ?? null,
                  error: ((data.error ?? data.previous_error) as string) ?? null,
                  started_at: null,
                  completed_at: null,
                  duration_ms: (data.duration_ms as number) ?? null,
                  retryAttempt: (data.attempt as number) ?? undefined,
                  maxRetries: (data.max_retries as number) ?? undefined,
                },
              }))
            } else if (eventType === "run_completed") {
              setFinalOutputs((data.outputs ?? null) as Record<string, unknown> | null)
              setRunDuration((data.duration_ms ?? null) as number | null)
              setIsRunning(false)
            } else if (eventType === "run_failed") {
              setFinalError((data.error ?? "Unknown error") as string)
              setRunDuration((data.duration_ms ?? null) as number | null)
              setIsRunning(false)
            } else if (eventType === "end") {
              setIsRunning(false)
            }
          } catch {
            // Ignore unparseable
          }
        }

        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split("\n")
          buffer = lines.pop() ?? ""
          for (const line of lines) {
            if (line.startsWith("event:")) {
              currentEvent = line.slice(6).trim()
            } else if (line.startsWith("data:")) {
              // SSE spec allows multi-line data — append with newline separator
              currentData = currentData
                ? currentData + "\n" + line.slice(5).trim()
                : line.slice(5).trim()
            } else if (line.startsWith(":")) {
              // SSE comment (keepalive) — ignore
            } else if (line === "") {
              if (currentData) dispatch(currentEvent, currentData)
              currentEvent = "message"
              currentData = ""
            }
          }
        }
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return
        setFinalError(err instanceof Error ? err.message : "Stream error")
        setIsRunning(false)
      }
    },
    [workflow],
  )

  const handleCancelRun = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setIsRunning(false)
    if (workflow) {
      workflowApi.cancelRun(workflow.id, "current").catch(() => {})
    }
  }, [workflow])

  const handleExport = useCallback(async () => {
    if (!workflow) return
    try {
      const data = await workflowApi.export(workflow.id)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      const slug = (workflow.name || "workflow")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
      a.download = `${slug}.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error(t("workflowExportFailed"))
    }
  }, [workflow, t])

  const handleImport = useCallback(() => {
    const input = document.createElement("input")
    input.type = "file"
    input.accept = ".json"
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const data = JSON.parse(text)
        // Support both envelope format { workflow: { blueprint } } and legacy { blueprint }
        const blueprint = data.workflow?.blueprint ?? data.blueprint
        if (blueprint) {
          blueprintRef.current = blueprint
          // Force re-render by updating workflow state
          setWorkflow((prev) =>
            prev ? { ...prev, blueprint } : prev,
          )
          setIsDirty(true)
          toast.success(t("workflowImported"))
        }
      } catch {
        toast.error(t("workflowImportFailed"))
      }
    }
    input.click()
  }, [t])

  const handleDuplicate = useCallback(async () => {
    if (!workflow) return
    setIsDuplicating(true)
    try {
      const duplicated = await workflowApi.duplicate(workflow.id)
      toast.success(t("workflowDuplicated"))
      router.push(`/workflows/${duplicated.id}`)
    } catch {
      toast.error(t("workflowDuplicateFailed"))
    } finally {
      setIsDuplicating(false)
    }
  }, [workflow, router, t])

  const handleDelete = useCallback(async () => {
    if (!workflow) return
    setShowDeleteDialog(false)
    try {
      await workflowApi.delete(workflow.id)
      toast.success(t("workflowDeleted"))
      router.replace("/workflows")
    } catch {
      toast.error(t("workflowDeleteFailed"))
    }
  }, [workflow, router, t])

  const handlePublishClick = useCallback(() => {
    setShowPublishDialog(true)
    setPublishOrgId("")
    setOrgsLoading(true)
    orgApi.list().then((orgs) => {
      setUserOrgs(orgs)
      if (orgs.length > 0) setPublishOrgId(orgs[0].id)
    }).catch(() => {}).finally(() => setOrgsLoading(false))
  }, [])

  const confirmPublish = useCallback(async () => {
    if (!workflow || !publishOrgId) return
    setShowPublishDialog(false)
    try {
      const updated = await workflowApi.publish(workflow.id, {
        scope: "org",
        org_id: publishOrgId,
      })
      setWorkflow(updated)
      toast.success(t("workflowPublished"))
    } catch {
      toast.error(t("workflowPublishFailed"))
    }
  }, [workflow, publishOrgId, t])

  const handleUnpublishClick = useCallback(() => {
    setShowUnpublishDialog(true)
  }, [])

  const confirmUnpublish = useCallback(async () => {
    if (!workflow) return
    setShowUnpublishDialog(false)
    try {
      const updated = await workflowApi.unpublish(workflow.id)
      setWorkflow(updated)
      toast.success(t("workflowUnpublished"))
    } catch {
      toast.error(t("workflowUnpublishFailed"))
    }
  }, [workflow, t])

  const handleResubmit = useCallback(async () => {
    if (!workflow) return
    try {
      const updated = await workflowApi.resubmit(workflow.id)
      setWorkflow(updated)
      toast.success(to("resubmitSuccess"))
    } catch {
      toast.error(to("resubmitFailed"))
    }
  }, [workflow, to])

  const handleAutoLayout = useCallback(() => {
    editorRef.current?.autoLayout()
  }, [])

  const handleVersionRestored = useCallback(() => {
    // Reload the workflow data from the server after a version restore
    workflowApi
      .get(workflowId)
      .then((data) => {
        setWorkflow(data)
        blueprintRef.current = data.blueprint ?? {
          nodes: [],
          edges: [],
          viewport: { x: 0, y: 0, zoom: 1 },
        }
        setIsDirty(false)
        setLastSavedAt(new Date())
        triggerValidation()
      })
      .catch(() => {
        toast.error(t("workflowUpdateFailed"))
      })
  }, [workflowId, t, triggerValidation])

  const handleValidate = useCallback(async () => {
    if (!workflow) return
    setServerValidationOpen(true)
    setIsServerValidating(true)
    try {
      const result = await workflowApi.validateById(workflow.id)
      setServerValidationResult(result)
    } catch {
      toast.error(t("validateFailed"))
    } finally {
      setIsServerValidating(false)
    }
  }, [workflow, t])

  // Find selected org for review notice
  const selectedOrg = publishOrgId
    ? userOrgs.find((o) => o.id === publishOrgId)
    : null

  // Get start variables for run panel
  const startNode = blueprintRef.current.nodes.find((n) => n.type === "start")
  const startData = startNode?.data as StartNodeData | undefined
  const startVariables = startData?.variables ?? []

  // Build nodeId -> nodeType map for display labels
  const nodeTypeMap = useMemo(() => {
    const map: Record<string, WorkflowNodeType> = {}
    for (const n of blueprintRef.current.nodes) {
      map[n.id] = n.type
    }
    return map
  }, [blueprintRef.current.nodes])

  // Total node count (excluding start/end for progress display)
  const totalNodeCount = useMemo(() => {
    return blueprintRef.current.nodes.filter(
      (n) => n.type !== "start" && n.type !== "end",
    ).length
  }, [blueprintRef.current.nodes])

  if (authLoading || !user) return null

  if (isLoadingWorkflow) {
    return (
      <div className="flex h-full items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!workflow) return null

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <WorkflowToolbar
        name={workflow.name}
        description={workflow.description}
        status={workflow.status}
        visibility={workflow.visibility}
        publishStatus={workflow.publish_status}
        isSaving={isSaving}
        isDirty={isDirty}
        lastSavedAt={lastSavedAt}
        isRunning={isRunning}
        isDuplicating={isDuplicating}
        isValidating={isValidating}
        validationResult={validationResult}
        canUndo={canUndo}
        canRedo={canRedo}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onNameChange={handleNameChange}
        onDescriptionChange={handleDescriptionChange}
        onSave={handleSave}
        onRun={handleRun}
        onExport={handleExport}
        onImport={handleImport}
        onDuplicate={handleDuplicate}
        onDelete={() => setShowDeleteDialog(true)}
        onHistory={() => setHistoryOpen(true)}
        onVersionHistory={() => setVersionHistoryOpen(true)}
        onStats={() => setStatsOpen(true)}
        onNodeStats={() => setNodeStatsOpen(true)}
        onAutoLayout={handleAutoLayout}
        onValidate={handleValidate}
        onPublish={handlePublishClick}
        onUnpublish={handleUnpublishClick}
        onResubmit={handleResubmit}
        onEnvVars={() => setShowEnvDialog(true)}
        onVariables={() => setVariablesPanelOpen(true)}
        onAnalytics={() => setAnalyticsOpen(true)}
      />

      <WorkflowEditor
        ref={editorRef}
        blueprint={workflow.blueprint}
        onBlueprintChange={handleBlueprintChange}
        onUndoRedoChange={handleUndoRedoChange}
        isRunning={isRunning}
        runPanelOpen={runPanelOpen}
        startVariables={startVariables}
        nodeResults={nodeResults}
        finalOutputs={finalOutputs}
        finalError={finalError}
        runDuration={runDuration}
        nodeTypeMap={nodeTypeMap}
        totalNodeCount={totalNodeCount}
        onStartRun={handleStartRun}
        onRunAgain={handleRunAgain}
        onCancelRun={handleCancelRun}
        onCloseRunPanel={() => setRunPanelOpen(false)}
      />

      {/* Delete Confirmation */}
      <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("deleteDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setShowDeleteDialog(false)}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" className="px-6" onClick={handleDelete}>
              {tc("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unsaved changes guard */}
      <Dialog open={showUnsavedDialog} onOpenChange={setShowUnsavedDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unsavedChangesTitle")}</DialogTitle>
            <DialogDescription>
              {t("unsavedChangesDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setShowUnsavedDialog(false)}>
              {t("stay")}
            </Button>
            <Button
              variant="destructive"
              className="px-6"
              onClick={() => {
                setShowUnsavedDialog(false)
                setIsDirty(false)
                if (pendingNavigationRef.current) {
                  router.push(pendingNavigationRef.current)
                }
              }}
            >
              {t("discardAndLeave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Publish Confirmation */}
      <Dialog open={showPublishDialog} onOpenChange={setShowPublishDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("publishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("publishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              {orgsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                </div>
              ) : userOrgs.length === 0 ? (
                <p className="text-sm text-muted-foreground">{t("publishNoOrgs")}</p>
              ) : (
                <>
                  <Select value={publishOrgId} onValueChange={setPublishOrgId}>
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t("publishSelectOrg")} />
                    </SelectTrigger>
                    <SelectContent>
                      {userOrgs.map((org) => (
                        <SelectItem key={org.id} value={org.id}>{org.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {/* Review notice */}
                  {selectedOrg?.review_workflows && (
                    <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 p-2 rounded-md">
                      <Clock className="h-4 w-4 shrink-0" />
                      <span>{to("publishRequiresReview")}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setShowPublishDialog(false)}>{tc("cancel")}</Button>
            <Button
              className="px-6"
              onClick={confirmPublish}
              disabled={orgsLoading || userOrgs.length === 0 || !publishOrgId}
            >
              {tc("publish")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unpublish Confirmation */}
      <Dialog open={showUnpublishDialog} onOpenChange={setShowUnpublishDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unpublishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("unpublishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => setShowUnpublishDialog(false)}>{tc("cancel")}</Button>
            <Button variant="secondary" className="px-6" onClick={confirmUnpublish}>{tc("unpublish")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Env Variables Dialog */}
      <EnvVarsDialog
        workflowId={workflowId}
        open={showEnvDialog}
        onOpenChange={setShowEnvDialog}
      />

      {/* Run History Sheet */}
      <RunHistorySheet
        workflowId={workflowId}
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        nodeTypeMap={nodeTypeMap}
      />

      {/* Version History Sheet */}
      <VersionHistorySheet
        workflowId={workflowId}
        open={versionHistoryOpen}
        onOpenChange={setVersionHistoryOpen}
        onVersionRestored={handleVersionRestored}
      />

      {/* Stats Panel */}
      <WorkflowStatsPanel
        workflowId={workflowId}
        open={statsOpen}
        onOpenChange={setStatsOpen}
      />

      {/* Node Stats Panel */}
      <NodeStatsPanel
        workflowId={workflowId}
        open={nodeStatsOpen}
        onOpenChange={setNodeStatsOpen}
        nodeTypeMap={nodeTypeMap}
      />

      {/* Validation Panel */}
      <ValidationPanel
        open={serverValidationOpen}
        onOpenChange={setServerValidationOpen}
        isLoading={isServerValidating}
        result={serverValidationResult}
      />

      {/* Variables Panel */}
      <VariablesPanel
        open={variablesPanelOpen}
        onOpenChange={setVariablesPanelOpen}
        variables={blueprintRef.current.variables ?? []}
        onChange={handleVariablesChange}
      />

      {/* Analytics Panel */}
      <AnalyticsPanel
        workflowId={workflowId}
        open={analyticsOpen}
        onOpenChange={setAnalyticsOpen}
        nodeTypeMap={nodeTypeMap}
      />
    </div>
  )
}
