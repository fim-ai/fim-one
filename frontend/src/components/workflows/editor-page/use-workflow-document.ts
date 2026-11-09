import { useCallback, useEffect, useRef, useState } from "react"
import type { MutableRefObject } from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { useTranslations } from "next-intl"

import { workflowApi } from "@/lib/api"
import { toastWorkflowError } from "@/lib/workflow-errors"
import type { WorkflowBlueprint, WorkflowResponse, WorkflowVariable } from "@/types/workflow"
import {
  blueprintOfImport,
  emptyBlueprint,
  exportFileName,
  inputSchemaOf,
  nodeLabelOf,
  outputSchemaOf,
  withVariablesOf,
} from "./blueprint-derived"

/** Idle time after the last edit before the workflow saves itself. */
const AUTO_SAVE_DELAY_MS = 5000

interface WorkflowDocumentOptions {
  workflowId: string
  /**
   * Id of the signed-in user; the workflow loads once there is one. The id,
   * not the user object: the object is replaced whenever the profile or the
   * session token is refreshed, which must not reload the workflow.
   */
  userId: string | null
  /** The blueprint being edited; the editor and the validation read it too. */
  blueprintRef: MutableRefObject<WorkflowBlueprint>
  /** Called when the blueprint was loaded or changed, to validate it again. */
  onBlueprintChanged: () => void
  /**
   * The canvas as it is right now, or null when there is none. The canvas
   * reports its changes after a short pause; a save reads it directly so
   * that it never sends a blueprint older than what is on screen.
   */
  readCanvas?: () => WorkflowBlueprint | null
}

/**
 * The workflow being edited as a document: loading it, its unsaved state,
 * saving (by hand, by Cmd+S, and by itself after a pause), and the
 * operations on the whole document (rename, status, import, export,
 * duplicate, delete, reload after a version restore).
 */
export function useWorkflowDocument({
  workflowId,
  userId,
  blueprintRef,
  onBlueprintChanged,
  readCanvas,
}: WorkflowDocumentOptions) {
  const t = useTranslations("workflows")
  const tError = useTranslations("errors")
  const router = useRouter()

  const [workflow, setWorkflow] = useState<WorkflowResponse | null>(null)
  const [isLoadingWorkflow, setIsLoadingWorkflow] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isDuplicating, setIsDuplicating] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null)
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isAutoSavingRef = useRef(false)
  // Snapshot of the blueprint at last save/load — used to detect real changes
  // vs. overlay-only updates (validation badges, run overlays) that shouldn't
  // mark the form dirty.
  const savedBlueprintRef = useRef<string>("")
  // Counts the times the blueprint was replaced as a whole (import, version
  // restore). The canvas keeps its own node state once mounted, so the page
  // keys the editor on this to have it start over from the new blueprint.
  const [canvasRevision, setCanvasRevision] = useState(0)

  // Clean up the auto-save timer on unmount
  useEffect(() => {
    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current)
      }
    }
  }, [])

  // Load workflow
  useEffect(() => {
    if (!userId || !workflowId) return
    let cancelled = false
    setIsLoadingWorkflow(true)
    workflowApi
      .get(workflowId)
      .then((data) => {
        if (cancelled) return
        setWorkflow(data)
        const bp = data.blueprint ?? emptyBlueprint()
        blueprintRef.current = bp
        savedBlueprintRef.current = JSON.stringify(bp)
        // Trigger initial validation after load
        onBlueprintChanged()
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
  }, [userId, workflowId, router, t, onBlueprintChanged, blueprintRef])

  // Dirty state beforeunload guard
  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [isDirty])

  const changeName = useCallback(
    async (name: string) => {
      if (!workflow) return
      setWorkflow((prev) => prev ? { ...prev, name } : prev)
      setIsDirty(true)
    },
    [workflow],
  )

  const changeIcon = useCallback(
    (icon: string | null) => {
      if (!workflow) return
      setWorkflow((prev) => prev ? { ...prev, icon } : prev)
      setIsDirty(true)
    },
    [workflow],
  )

  const changeDescription = useCallback(
    (description: string) => {
      if (!workflow) return
      setWorkflow((prev) => prev ? { ...prev, description } : prev)
      setIsDirty(true)
    },
    [workflow],
  )

  const changeStatus = useCallback(
    async (status: "draft" | "active") => {
      if (!workflow || workflow.status === status) return
      try {
        const updated = await workflowApi.update(workflow.id, { status })
        setWorkflow(updated)
        toast.success(t("statusChanged"))
      } catch {
        toast.error(t("workflowUpdateFailed"))
      }
    },
    [workflow, t],
  )

  const save = useCallback(async () => {
    if (!workflow) return
    const isAutoSave = isAutoSavingRef.current
    // Only show saving indicator for manual saves
    if (!isAutoSave) setIsSaving(true)
    // Edits the canvas has not reported yet are part of what is saved.
    const canvas = readCanvas?.()
    if (canvas) blueprintRef.current = withVariablesOf(canvas, blueprintRef.current)
    try {
      const updated = await workflowApi.update(workflow.id, {
        name: workflow.name,
        description: workflow.description,
        icon: workflow.icon,
        blueprint: blueprintRef.current,
        input_schema: inputSchemaOf(blueprintRef.current),
        output_schema: outputSchemaOf(blueprintRef.current),
      })
      setWorkflow(updated)
      savedBlueprintRef.current = JSON.stringify(blueprintRef.current)
      setIsDirty(false)
      setLastSavedAt(new Date())
    } catch (err) {
      // A failed auto-save stays silent when it may just be the network,
      // but a save the server refused (an invalid setting, a switched-off
      // step) is shown, naming the steps at fault: retrying will not fix
      // it, and the edits would otherwise be lost without a word.
      toastWorkflowError(
        err,
        isAutoSave ? null : t("workflowUpdateFailed"),
        tError,
        t,
        (nodeId) => nodeLabelOf(blueprintRef.current, nodeId),
      )
    } finally {
      if (!isAutoSave) setIsSaving(false)
      isAutoSavingRef.current = false
    }
  }, [workflow, t, tError, blueprintRef, readCanvas])

  /** Take a changed blueprint: mark unsaved, validate, and save after a pause. */
  const applyBlueprint = useCallback((bp: WorkflowBlueprint) => {
    blueprintRef.current = bp

    // Compare against last-saved snapshot to avoid false dirty flags from
    // overlay-only updates (validation badges, run status overlays).
    const bpStr = JSON.stringify(bp)
    if (bpStr === savedBlueprintRef.current) {
      return // No real change — skip dirty flag and auto-save
    }

    setIsDirty(true)
    onBlueprintChanged()

    // Auto-save after 5 seconds of inactivity (silent — no "Saving..." indicator)
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
    }
    autoSaveTimerRef.current = setTimeout(() => {
      isAutoSavingRef.current = true
      save()
    }, AUTO_SAVE_DELAY_MS)
  }, [onBlueprintChanged, save, blueprintRef])

  /**
   * The canvas changed. It knows nodes, edges and the viewport; the
   * workflow variables are edited elsewhere and are kept as they are.
   */
  const changeBlueprint = useCallback(
    (canvas: WorkflowBlueprint) => {
      applyBlueprint(withVariablesOf(canvas, blueprintRef.current))
    },
    [applyBlueprint, blueprintRef],
  )

  const changeVariables = useCallback(
    (variables: WorkflowVariable[]) => {
      const updated = { ...blueprintRef.current, variables }
      applyBlueprint(updated)
    },
    [applyBlueprint, blueprintRef],
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
          save()
        }
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [isSaving, save])

  const exportToFile = useCallback(async () => {
    if (!workflow) return
    try {
      const data = await workflowApi.export(workflow.id)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = exportFileName(workflow.name)
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error(t("workflowExportFailed"))
    }
  }, [workflow, t])

  /** Replace the blueprint being edited with the one in an exported file's text. */
  const importFromText = useCallback(
    (text: string) => {
      let data: unknown
      try {
        data = JSON.parse(text)
      } catch {
        toast.error(t("workflowImportFailed"))
        return
      }
      // Support both envelope format { workflow: { blueprint } } and legacy { blueprint }
      const blueprint = blueprintOfImport(data)
      if (!blueprint) {
        toast.error(t("importInvalidFormat"))
        return
      }
      if (!Array.isArray(blueprint.nodes) || !Array.isArray(blueprint.edges)) {
        toast.error(t("importInvalidBlueprint"))
        return
      }
      blueprintRef.current = blueprint
      setWorkflow((prev) =>
        prev ? { ...prev, blueprint } : prev,
      )
      // The canvas starts over from the imported blueprint; its first report
      // then validates it and schedules the save like any other edit.
      setCanvasRevision((n) => n + 1)
      setIsDirty(true)
      toast.success(t("workflowImported"))
    },
    [t, blueprintRef],
  )

  const importFromFile = useCallback(() => {
    const input = document.createElement("input")
    input.type = "file"
    input.accept = ".json"
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      try {
        importFromText(await file.text())
      } catch {
        toast.error(t("workflowImportFailed"))
      }
    }
    input.click()
  }, [t, importFromText])

  const duplicate = useCallback(async () => {
    if (!workflow) return
    setIsDuplicating(true)
    try {
      const duplicated = await workflowApi.duplicate(workflow.id)
      toast.success(t("workflowDuplicated"))
      router.push(`/workflows/${duplicated.id}`)
    } catch (err) {
      toastWorkflowError(err, t("workflowDuplicateFailed"), tError, t)
    } finally {
      setIsDuplicating(false)
    }
  }, [workflow, router, t, tError])

  const deleteWorkflow = useCallback(async () => {
    if (!workflow) return
    try {
      await workflowApi.delete(workflow.id)
      toast.success(t("workflowDeleted"))
      router.replace("/workflows")
    } catch {
      toast.error(t("workflowDeleteFailed"))
    }
  }, [workflow, router, t])

  /** Reload the workflow from the server after a version restore. */
  const reloadAfterRestore = useCallback(() => {
    workflowApi
      .get(workflowId)
      .then((data) => {
        setWorkflow(data)
        const bp = data.blueprint ?? emptyBlueprint()
        blueprintRef.current = bp
        savedBlueprintRef.current = JSON.stringify(bp)
        // What was on the canvas is superseded: drop its pending save and
        // have the canvas start over from the restored blueprint.
        if (autoSaveTimerRef.current) {
          clearTimeout(autoSaveTimerRef.current)
          autoSaveTimerRef.current = null
        }
        setCanvasRevision((n) => n + 1)
        setIsDirty(false)
        setLastSavedAt(new Date())
        onBlueprintChanged()
      })
      .catch(() => {
        toast.error(t("workflowUpdateFailed"))
      })
  }, [workflowId, t, onBlueprintChanged, blueprintRef])

  return {
    workflow,
    setWorkflow,
    isLoadingWorkflow,
    isSaving,
    isDuplicating,
    isDirty,
    setIsDirty,
    lastSavedAt,
    canvasRevision,
    changeName,
    changeIcon,
    changeDescription,
    changeStatus,
    save,
    changeBlueprint,
    changeVariables,
    exportToFile,
    importFromFile,
    importFromText,
    duplicate,
    deleteWorkflow,
    reloadAfterRestore,
  }
}
