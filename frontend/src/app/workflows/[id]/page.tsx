"use client"

import { useEffect, useCallback, useRef, useState } from "react"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useTranslations } from "next-intl"
import { Loader2 } from "lucide-react"
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
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { PublishDialog } from "@/components/shared/publish-dialog"
import { useAuth } from "@/contexts/auth-context"
import { workflowApi } from "@/lib/api"
import { WorkflowToolbar } from "@/components/workflows/workflow-toolbar"
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
import { WebhookConfigDialog } from "@/components/workflows/webhook-config-dialog"
import { ApiKeyDialog } from "@/components/workflows/api-key-dialog"
import { ScheduleDialog } from "@/components/workflows/schedule-dialog"
import { BatchRunDialog } from "@/components/workflows/batch-run-dialog"
import { emptyBlueprint, startChecksInputs, startVariablesOf } from "@/components/workflows/editor-page/blueprint-derived"
import { useLeaveGuard } from "@/components/workflows/editor-page/use-leave-guard"
import { useOpenPanels } from "@/components/workflows/editor-page/use-open-panels"
import { useWorkflowDocument } from "@/components/workflows/editor-page/use-workflow-document"
import { useWorkflowPublishing } from "@/components/workflows/editor-page/use-workflow-publishing"
import { useWorkflowRun } from "@/components/workflows/editor-page/use-workflow-run"
import { isActiveRunStatus } from "@/components/workflows/editor-page/run-record"
import { useWorkflowValidation } from "@/components/workflows/editor-page/use-workflow-validation"
import type {
  WorkflowBlueprint,
  NodeRunResult,
} from "@/types/workflow"
import { usePageTitle } from "@/hooks/use-page-title"

/** The dialogs and side sheets the toolbar opens. */
type EditorPanel =
  | "delete"
  | "envVars"
  | "runHistory"
  | "versionHistory"
  | "stats"
  | "nodeStats"
  | "variables"
  | "analytics"
  | "webhook"
  | "apiKey"
  | "schedule"
  | "batchRun"

export default function WorkflowEditorPage() {
  const t = useTranslations("workflows")
  const tc = useTranslations("common")
  const { user, isLoading: authLoading } = useAuth()
  const router = useRouter()
  const params = useParams()
  const searchParams = useSearchParams()
  const workflowId = params.id as string

  const editorRef = useRef<WorkflowEditorHandle>(null)
  // The blueprint being edited. The canvas writes it, saving reads it.
  const blueprintRef = useRef<WorkflowBlueprint>(emptyBlueprint())

  // --- State, one hook per concern ---
  const panels = useOpenPanels<EditorPanel>()
  const validation = useWorkflowValidation(workflowId, blueprintRef, editorRef)
  const readCanvas = useCallback(() => editorRef.current?.getBlueprint() ?? null, [])
  const doc = useWorkflowDocument({
    workflowId,
    userId: user?.id ?? null,
    blueprintRef,
    onBlueprintChanged: validation.triggerValidation,
    readCanvas,
  })
  const { workflow, setWorkflow, isLoadingWorkflow, discardChanges } = doc
  // A run executes the stored workflow: what is on the canvas is saved first.
  const run = useWorkflowRun(workflow?.id ?? null, doc.saveBeforeRun)
  const publishing = useWorkflowPublishing(workflow, setWorkflow)
  // With unsaved changes, a link to another page asks before it is followed.
  const leaveGuard = useLeaveGuard(doc.hasUnsavedChanges)
  // While the "Unsaved changes" question is open the document does not save
  // itself; "Stay" resumes it, "Discard & Leave" gives the edits up.
  const { holdAutoSave, resumeAutoSave } = doc
  const leaveQuestionOpen = leaveGuard.pendingHref !== null
  useEffect(() => {
    if (!leaveQuestionOpen) return
    holdAutoSave()
    return resumeAutoSave
  }, [leaveQuestionOpen, holdAutoSave, resumeAutoSave])

  usePageTitle(workflow?.name)

  // Schedule status (shown as a dot in the toolbar menu)
  const [scheduleActive, setScheduleActive] = useState(false)

  // Undo/redo state (synced from editor via callback)
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

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

  // Auth guard
  useEffect(() => {
    if (!authLoading && !user) {
      router.replace("/login")
    }
  }, [authLoading, user, router])

  // Auto-collapse sidebar when entering workflow editor
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("builder-mode-change", { detail: { active: true } }),
    )
    return () => {
      window.dispatchEvent(
        new CustomEvent("builder-mode-change", { detail: { active: false } }),
      )
    }
  }, [])

  // Load schedule status
  const userId = user?.id ?? null
  useEffect(() => {
    if (!userId || !workflowId) return
    workflowApi
      .getSchedule(workflowId)
      .then((data) => {
        setScheduleActive(data.enabled)
      })
      .catch(() => {
        // No schedule configured — that's fine
        setScheduleActive(false)
      })
  }, [userId, workflowId])

  // A run of this workflow that the user started and that has not ended
  // (it runs, or waits for a decision) is shown again on opening the page.
  const { attachToRun } = run
  const loadedWorkflowId = workflow?.id ?? null
  useEffect(() => {
    if (!userId || !loadedWorkflowId) return
    let cancelled = false
    workflowApi
      .getRuns(loadedWorkflowId, 1, 1)
      .then((page) => {
        const latest = page.items[0]
        if (cancelled || !latest) return
        if (latest.user_id === userId && isActiveRunStatus(latest.status)) attachToRun(latest)
      })
      .catch(() => {
        // Nothing to show again; the run history still lists it.
      })
    return () => {
      cancelled = true
    }
  }, [userId, loadedWorkflowId, attachToRun])

  // Auto-open run panel when navigated with ?run=true
  const { openRunPanel } = run
  useEffect(() => {
    if (searchParams.get("run") === "true" && workflow && !isLoadingWorkflow) {
      openRunPanel()
      router.replace(`/workflows/${workflowId}`, { scroll: false })
    }
  }, [searchParams, workflow, isLoadingWorkflow, workflowId, router, openRunPanel])

  const { deleteWorkflow } = doc
  const { set: setPanelOpen } = panels
  const handleDelete = useCallback(async () => {
    setPanelOpen("delete", false)
    await deleteWorkflow()
  }, [deleteWorkflow, setPanelOpen])

  const handleWebhookSaved = useCallback((webhookUrl: string | null) => {
    setWorkflow((prev) => prev ? { ...prev, webhook_url: webhookUrl } : prev)
  }, [setWorkflow])

  const handleApiKeyChanged = useCallback((hasKey: boolean) => {
    setWorkflow((prev) => prev ? { ...prev, has_api_key: hasKey } : prev)
  }, [setWorkflow])

  const handleScheduleChange = useCallback((hasSchedule: boolean) => {
    setScheduleActive(hasSchedule)
  }, [])

  const handleAutoLayout = useCallback(() => {
    editorRef.current?.autoLayout()
  }, [])

  const handleViewRunOnCanvas = useCallback((overlayNodeResults: Record<string, NodeRunResult>) => {
    editorRef.current?.applyRunOverlay(overlayNodeResults)
  }, [])

  // Get start variables for run panel
  const startVariables = startVariablesOf(blueprintRef.current)

  // nodeId -> node type, for the panels that name steps
  const { nodeTypeMap } = doc

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
        icon={workflow.icon}
        description={workflow.description}
        status={workflow.status}
        visibility={workflow.visibility}
        publishStatus={workflow.publish_status}
        isSaving={doc.isSaving}
        isDirty={doc.isDirty}
        lastSavedAt={doc.lastSavedAt}
        isRunning={run.isRunning}
        isDuplicating={doc.isDuplicating}
        isValidating={validation.isValidating}
        validationResult={validation.validationResult}
        canUndo={canUndo}
        canRedo={canRedo}
        onUndo={handleUndo}
        onRedo={handleRedo}
        onNameChange={doc.changeName}
        onIconChange={doc.changeIcon}
        onDescriptionChange={doc.changeDescription}
        onSave={doc.save}
        onRun={run.openRunPanel}
        onExport={doc.exportToFile}
        onImport={doc.importFromFile}
        onDuplicate={doc.duplicate}
        onDelete={panels.opener("delete")}
        onHistory={panels.opener("runHistory")}
        onVersionHistory={panels.opener("versionHistory")}
        onStats={panels.opener("stats")}
        onNodeStats={panels.opener("nodeStats")}
        onAutoLayout={handleAutoLayout}
        onValidate={validation.validateOnServer}
        onPublish={publishing.openPublishDialog}
        onUnpublish={publishing.openUnpublishDialog}
        onResubmit={publishing.resubmit}
        onEnvVars={panels.opener("envVars")}
        onVariables={panels.opener("variables")}
        onAnalytics={panels.opener("analytics")}
        onWebhook={panels.opener("webhook")}
        webhookConfigured={!!workflow.webhook_url}
        onApiKey={panels.opener("apiKey")}
        apiKeyConfigured={!!workflow.has_api_key}
        onSchedule={panels.opener("schedule")}
        scheduleActive={scheduleActive}
        onStatusChange={doc.changeStatus}
        onBatchRun={panels.opener("batchRun")}
      />

      <WorkflowEditor
        // A new key when the blueprint was replaced as a whole (import,
        // version restore): the canvas starts over from it.
        key={doc.canvasRevision}
        saveBeforeRun={doc.saveBeforeRun}
        ref={editorRef}
        workflowId={workflowId}
        blueprint={workflow.blueprint}
        onBlueprintChange={doc.changeBlueprint}
        onUndoRedoChange={handleUndoRedoChange}
        isRunning={run.isRunning || run.isWaiting}
        runStatus={run.runStatus}
        waitingFor={run.waitingFor}
        onRunDecided={run.refreshRun}
        runPanelOpen={run.runPanelOpen}
        startVariables={startVariables}
        checkInputs={startChecksInputs(blueprintRef.current)}
        inputError={run.inputError}
        onClearInputError={run.clearInputError}
        nodeResults={run.nodeResults}
        finalOutputs={run.finalOutputs}
        finalError={run.finalError}
        runDuration={run.runDuration}
        nodeTypeMap={nodeTypeMap}
        // The run executes the stored workflow: its own step count, once it
        // has started (0 hides the progress until then).
        totalNodeCount={run.totalNodeCount ?? 0}
        logEvents={run.logEvents}
        onStartRun={run.startRun}
        onRunAgain={run.runAgain}
        onCancelRun={run.cancelRun}
        onCloseRunPanel={run.closeRunPanel}
      />

      {/* Delete Confirmation */}
      <Dialog open={panels.isOpen("delete")} onOpenChange={panels.setter("delete")}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("deleteDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => panels.set("delete", false)}>
              {tc("cancel")}
            </Button>
            <Button variant="destructive" className="px-6" onClick={handleDelete}>
              {tc("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Unsaved changes guard */}
      <Dialog
        open={leaveGuard.pendingHref !== null}
        onOpenChange={(open) => {
          if (!open) leaveGuard.stay()
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unsavedChangesTitle")}</DialogTitle>
            <DialogDescription>
              {t("unsavedChangesDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={leaveGuard.stay}>
              {t("stay")}
            </Button>
            <Button
              variant="destructive"
              className="px-6"
              onClick={() => {
                const destination = leaveGuard.pendingHref
                leaveGuard.stay()
                discardChanges()
                if (destination) router.push(destination)
              }}
            >
              {t("discardAndLeave")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import replaces the canvas: ask first when there is something on it */}
      <AlertDialog
        open={doc.hasPendingImport}
        onOpenChange={(open) => {
          if (!open) doc.cancelImport()
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("importReplaceTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("importReplaceDescription")}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc("cancel")}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={doc.confirmImport}>
              {t("importReplaceConfirm")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Publish Confirmation */}
      <PublishDialog
        open={publishing.showPublishDialog}
        onOpenChange={publishing.setShowPublishDialog}
        title={t("publishDialogTitle")}
        description={t("publishDialogDescription")}
        orgs={publishing.userOrgs}
        orgsLoading={publishing.orgsLoading}
        selectedOrgId={publishing.publishOrgId}
        onOrgChange={publishing.setPublishOrgId}
        requiresReview={!!publishing.selectedOrg?.review_workflows}
        noOrgsText={t("publishNoOrgs")}
        selectOrgPlaceholder={t("publishSelectOrg")}
        onConfirm={publishing.confirmPublish}
        resourceType="workflow"
        resourceId={workflowId}
      />

      {/* Unpublish Confirmation */}
      <Dialog open={publishing.showUnpublishDialog} onOpenChange={publishing.setShowUnpublishDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("unpublishDialogTitle")}</DialogTitle>
            <DialogDescription>
              {t("unpublishDialogDescription")}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" className="px-6" onClick={() => publishing.setShowUnpublishDialog(false)}>{tc("cancel")}</Button>
            <Button variant="secondary" className="px-6" onClick={publishing.confirmUnpublish}>{tc("unpublish")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Env Variables Dialog */}
      <EnvVarsDialog
        workflowId={workflowId}
        open={panels.isOpen("envVars")}
        onOpenChange={panels.setter("envVars")}
      />

      {/* Run History Sheet */}
      <RunHistorySheet
        workflowId={workflowId}
        open={panels.isOpen("runHistory")}
        onOpenChange={panels.setter("runHistory")}
        nodeTypeMap={nodeTypeMap}
        onViewRunOnCanvas={handleViewRunOnCanvas}
      />

      {/* Version History Sheet */}
      <VersionHistorySheet
        workflowId={workflowId}
        open={panels.isOpen("versionHistory")}
        onOpenChange={panels.setter("versionHistory")}
        onBeforeRestore={doc.beginRestore}
        onRestoreFailed={doc.cancelRestore}
        onVersionRestored={doc.reloadAfterRestore}
      />

      {/* Stats Panel */}
      <WorkflowStatsPanel
        workflowId={workflowId}
        open={panels.isOpen("stats")}
        onOpenChange={panels.setter("stats")}
      />

      {/* Node Stats Panel */}
      <NodeStatsPanel
        workflowId={workflowId}
        open={panels.isOpen("nodeStats")}
        onOpenChange={panels.setter("nodeStats")}
        nodeTypeMap={nodeTypeMap}
      />

      {/* Validation Panel */}
      <ValidationPanel
        open={validation.serverValidationOpen}
        onOpenChange={validation.setServerValidationOpen}
        isLoading={validation.isServerValidating}
        result={validation.serverValidationResult}
      />

      {/* Variables Panel */}
      <VariablesPanel
        open={panels.isOpen("variables")}
        onOpenChange={panels.setter("variables")}
        variables={blueprintRef.current.variables ?? []}
        onChange={doc.changeVariables}
      />

      {/* Analytics Panel */}
      <AnalyticsPanel
        workflowId={workflowId}
        open={panels.isOpen("analytics")}
        onOpenChange={panels.setter("analytics")}
        nodeTypeMap={nodeTypeMap}
      />

      {/* Webhook Config Dialog */}
      <WebhookConfigDialog
        workflowId={workflowId}
        webhookUrl={workflow.webhook_url}
        open={panels.isOpen("webhook")}
        onOpenChange={panels.setter("webhook")}
        onSaved={handleWebhookSaved}
      />

      {/* API Key Dialog */}
      <ApiKeyDialog
        workflowId={workflowId}
        hasApiKey={!!workflow.has_api_key}
        open={panels.isOpen("apiKey")}
        onOpenChange={panels.setter("apiKey")}
        onApiKeyChanged={handleApiKeyChanged}
      />

      {/* Schedule Config Dialog */}
      <ScheduleDialog
        workflowId={workflowId}
        open={panels.isOpen("schedule")}
        onOpenChange={panels.setter("schedule")}
        onScheduleChange={handleScheduleChange}
      />

      {/* Batch Run Dialog */}
      <BatchRunDialog
        workflowId={workflowId}
        beforeRun={doc.saveBeforeRun}
        open={panels.isOpen("batchRun")}
        onOpenChange={panels.setter("batchRun")}
      />
    </div>
  )
}
