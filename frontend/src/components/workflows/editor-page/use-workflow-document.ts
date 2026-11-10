import { useCallback, useEffect, useRef, useState } from "react"
import type { Dispatch, MutableRefObject, SetStateAction } from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"
import { useTranslations } from "next-intl"

import { workflowApi } from "@/lib/api"
import { toastWorkflowError } from "@/lib/workflow-errors"
import type {
  WorkflowBlueprint,
  WorkflowNodeType,
  WorkflowResponse,
  WorkflowVariable,
} from "@/types/workflow"
import {
  blueprintOfImport,
  emptyBlueprint,
  exportFileName,
  inputSchemaOf,
  nodeLabelOf,
  nodeTypeMapOf,
  outputSchemaOf,
  sameNodeTypes,
  withVariablesOf,
} from "./blueprint-derived"

/** Idle time after the last edit before the workflow saves itself. */
export const AUTO_SAVE_DELAY_MS = 5000

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
 * What a save sends, and so what the server holds once it succeeded: the
 * blueprint as JSON text plus the fields edited in the toolbar. "Unsaved"
 * means the document differs from the last one of these the server accepted.
 */
interface DocumentSnapshot {
  blueprintText: string
  /** The blueprint without its viewport: what an edit changes, as opposed to a pan or zoom. */
  graphText: string
  name: string
  description: string | null
  icon: string | null
}

/** The blueprint as JSON text without where the canvas is panned and zoomed to. */
function graphTextOf(blueprint: WorkflowBlueprint): string {
  return JSON.stringify({ ...blueprint, viewport: undefined })
}

/** A server copy of the workflow with the fields being edited locally kept as they are. */
function keepingLocalEdits(server: WorkflowResponse, local: WorkflowResponse): WorkflowResponse {
  return {
    ...server,
    name: local.name,
    description: local.description,
    icon: local.icon,
    blueprint: local.blueprint,
  }
}

/**
 * The workflow being edited as a document: loading it, its unsaved state,
 * saving, and the operations on the whole document (rename, status, import,
 * export, duplicate, delete, reload after a version restore).
 *
 * Saving has one path. Save, Cmd+S and the automatic save after a pause all
 * go through `runSave`, which
 * - cancels the pending automatic save (it is about to be done),
 * - lets one request be in flight at a time (a later one waits its turn),
 * - reads the canvas and the toolbar fields at the moment it sends, never
 *   from a closure taken when it was scheduled,
 * - records what it sent as "what the server holds" only when the server
 *   accepted it, and compares the document against that afterwards: an edit
 *   made while the request was in flight leaves the document unsaved and
 *   schedules the next save.
 *
 * Pan and zoom are stored in the blueprint, so a pan or zoom alone is saved
 * by the same automatic save. It is not an edit, though: it does not show
 * the document as unsaved and does not hold up leaving the page (leaving
 * before the save went out loses only where the canvas was scrolled to).
 *
 * Whatever replaces the document as a whole (loading, a version restore)
 * starts a new epoch; a save that began in an earlier epoch has its outcome
 * ignored. While saving is suspended (leaving, deleting) no save starts.
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

  const [workflow, setWorkflowState] = useState<WorkflowResponse | null>(null)
  const [isLoadingWorkflow, setIsLoadingWorkflow] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isDuplicating, setIsDuplicating] = useState(false)
  const [isDirty, setIsDirty] = useState(false)
  const [lastSavedAt, setLastSavedAt] = useState<Date | null>(null)
  // Counts the times the blueprint was replaced as a whole (import, version
  // restore). The canvas keeps its own node state once mounted, so the page
  // keys the editor on this to have it start over from the new blueprint.
  const [canvasRevision, setCanvasRevision] = useState(0)
  // A blueprint read from an import file, waiting for the user to confirm
  // that it may replace what is on the canvas.
  const [pendingImport, setPendingImport] = useState<WorkflowBlueprint | null>(null)
  // nodeId -> node type of the blueprint in use, for the panels that name
  // steps (run panel, run history, statistics). State rather than read off
  // the ref, so those panels follow the canvas; replaced only when a node
  // was added, removed or changed type.
  const [nodeTypeMap, setNodeTypeMap] = useState<Record<string, WorkflowNodeType>>({})
  const trackNodeTypes = useCallback((bp: WorkflowBlueprint) => {
    const next = nodeTypeMapOf(bp)
    setNodeTypeMap((prev) => (sameNodeTypes(prev, next) ? prev : next))
  }, [])

  // The workflow as last set, readable outside render: a save takes the name,
  // description and icon from here at the moment it sends.
  const workflowRef = useRef<WorkflowResponse | null>(null)
  const setWorkflow: Dispatch<SetStateAction<WorkflowResponse | null>> = useCallback((action) => {
    const next = typeof action === "function" ? action(workflowRef.current) : action
    workflowRef.current = next
    setWorkflowState(next)
  }, [])

  // What the server holds, as far as this page knows (set on load and by
  // each accepted save).
  const savedRef = useRef<DocumentSnapshot | null>(null)
  const autoSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // The save request in flight, if any. It never rejects.
  const inFlightRef = useRef<Promise<void> | null>(null)
  // Incremented when the document is replaced as a whole.
  const epochRef = useRef(0)
  // No save starts while this is set.
  const suspendedRef = useRef(false)
  // Manual saves waiting or in flight (drives the "Saving..." indicator).
  const manualSavesRef = useRef(0)

  // Latest callbacks, so that neither loading nor a scheduled save depends
  // on the identity a caller happened to pass at some earlier render.
  const onBlueprintChangedRef = useRef(onBlueprintChanged)
  onBlueprintChangedRef.current = onBlueprintChanged
  const readCanvasRef = useRef(readCanvas)
  readCanvasRef.current = readCanvas

  const currentSnapshot = useCallback((): DocumentSnapshot | null => {
    const wf = workflowRef.current
    if (!wf) return null
    return {
      blueprintText: JSON.stringify(blueprintRef.current),
      graphText: graphTextOf(blueprintRef.current),
      name: wf.name,
      description: wf.description,
      icon: wf.icon,
    }
  }, [blueprintRef])

  /** Whether the document differs from what the server holds: whether there is anything to save. */
  const isUnsaved = useCallback((): boolean => {
    const current = currentSnapshot()
    const saved = savedRef.current
    if (!current || !saved) return false
    return (
      current.blueprintText !== saved.blueprintText ||
      current.name !== saved.name ||
      current.description !== saved.description ||
      current.icon !== saved.icon
    )
  }, [currentSnapshot])

  /**
   * Whether there are edits the server does not hold: the same, leaving out
   * the viewport. This is what "unsaved changes" means to the user.
   */
  const hasUnsavedEdits = useCallback((): boolean => {
    const current = currentSnapshot()
    const saved = savedRef.current
    if (!current || !saved) return false
    return (
      current.graphText !== saved.graphText ||
      current.name !== saved.name ||
      current.description !== saved.description ||
      current.icon !== saved.icon
    )
  }, [currentSnapshot])

  const cancelAutoSave = useCallback(() => {
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current)
      autoSaveTimerRef.current = null
    }
  }, [])

  // `runSave` and `scheduleAutoSave` call each other; the timer goes through a ref.
  const runSaveRef = useRef<(mode: "manual" | "auto") => Promise<boolean>>(async () => false)

  /** (Re)start the pause after which the document saves itself. */
  const scheduleAutoSave = useCallback(() => {
    cancelAutoSave()
    if (suspendedRef.current) return
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveTimerRef.current = null
      void runSaveRef.current("auto")
    }, AUTO_SAVE_DELAY_MS)
  }, [cancelAutoSave])

  /** After any edit: update the unsaved marker, and save after a pause if there is something to save. */
  const noteEdit = useCallback(() => {
    setIsDirty(hasUnsavedEdits())
    if (isUnsaved()) scheduleAutoSave()
    else cancelAutoSave()
  }, [isUnsaved, hasUnsavedEdits, scheduleAutoSave, cancelAutoSave])

  /**
   * Resolves true when the server accepted the save (or an automatic save
   * had nothing to send), false when it failed or could not be made.
   */
  const runSave = useCallback(
    async (mode: "manual" | "auto"): Promise<boolean> => {
      // Whichever way a save was asked for, the pending automatic one is this one.
      cancelAutoSave()
      if (suspendedRef.current) return false
      const manual = mode === "manual"
      if (manual) {
        manualSavesRef.current += 1
        setIsSaving(true)
      }
      try {
        // One request at a time: the server must receive saves in the order
        // they were made, and each must send the document as it then is.
        while (inFlightRef.current) await inFlightRef.current
        if (suspendedRef.current) return false
        const wf = workflowRef.current
        if (!wf) return false

        // Edits the canvas has not reported yet are part of what is saved.
        const canvas = readCanvasRef.current?.()
        if (canvas) {
          blueprintRef.current = withVariablesOf(canvas, blueprintRef.current)
          trackNodeTypes(blueprintRef.current)
        }
        // An automatic save with nothing to save (an earlier save already
        // took the edit) does not send anything.
        if (!manual && !isUnsaved()) {
          setIsDirty(false)
          return true
        }

        const epoch = epochRef.current
        const blueprint = blueprintRef.current
        const sent: DocumentSnapshot = {
          blueprintText: JSON.stringify(blueprint),
          graphText: graphTextOf(blueprint),
          name: wf.name,
          description: wf.description,
          icon: wf.icon,
        }
        let accepted = false
        const request = (async () => {
          try {
            const updated = await workflowApi.update(wf.id, {
              name: sent.name,
              description: sent.description,
              icon: sent.icon,
              blueprint,
              input_schema: inputSchemaOf(blueprint),
              output_schema: outputSchemaOf(blueprint),
            })
            // The document was replaced while this was in flight: the answer
            // is about a document that is no longer being edited.
            if (epochRef.current !== epoch) return
            accepted = true
            savedRef.current = sent
            setWorkflow((local) => (local ? keepingLocalEdits(updated, local) : local))
            setLastSavedAt(new Date())
            // Saved is what was sent. If the document moved on meanwhile it
            // is still unsaved, and saves itself after the usual pause.
            noteEdit()
          } catch (err) {
            if (epochRef.current !== epoch) return
            // A failed auto-save stays silent when it may just be the network,
            // but a save the server refused (an invalid setting, a switched-off
            // step) is shown, naming the steps at fault: retrying will not fix
            // it, and the edits would otherwise be lost without a word.
            toastWorkflowError(
              err,
              manual ? t("workflowUpdateFailed") : null,
              tError,
              t,
              (nodeId) => nodeLabelOf(blueprintRef.current, nodeId),
            )
          } finally {
            inFlightRef.current = null
          }
        })()
        inFlightRef.current = request
        await request
        return accepted
      } finally {
        if (manual) {
          manualSavesRef.current -= 1
          if (manualSavesRef.current === 0) setIsSaving(false)
        }
      }
    },
    [cancelAutoSave, blueprintRef, trackNodeTypes, isUnsaved, setWorkflow, noteEdit, t, tError],
  )
  runSaveRef.current = runSave

  /** Save now (the Save button and Cmd+S). */
  const save = useCallback(async () => {
    await runSave("manual")
  }, [runSave])

  /**
   * A run executes the workflow the server holds, not the canvas. Before
   * one starts, whatever is not saved yet is saved: an edit waiting out the
   * automatic save's pause, one the canvas has not even reported, a save
   * already on its way. Resolves true when the server holds what was on
   * screen, false when the save failed (the failure is shown as for Save);
   * the run must not start then.
   */
  const saveBeforeRun = useCallback(async (): Promise<boolean> => {
    // Nothing may be saved (a version restore is under way): the run takes
    // what the server holds.
    if (suspendedRef.current) return true
    while (inFlightRef.current) await inFlightRef.current
    const canvas = readCanvasRef.current?.()
    if (canvas) {
      blueprintRef.current = withVariablesOf(canvas, blueprintRef.current)
      trackNodeTypes(blueprintRef.current)
    }
    if (!isUnsaved()) return true
    return runSave("manual")
  }, [blueprintRef, trackNodeTypes, isUnsaved, runSave])

  // Leaving the page: nothing may be saved from here on.
  useEffect(() => {
    suspendedRef.current = false
    return () => {
      suspendedRef.current = true
      cancelAutoSave()
    }
  }, [cancelAutoSave])

  // Load workflow
  useEffect(() => {
    if (!userId || !workflowId) return
    let cancelled = false
    epochRef.current += 1
    cancelAutoSave()
    setIsLoadingWorkflow(true)
    workflowApi
      .get(workflowId)
      .then((data) => {
        if (cancelled) return
        setWorkflow(data)
        const bp = data.blueprint ?? emptyBlueprint()
        blueprintRef.current = bp
        savedRef.current = currentSnapshot()
        setIsDirty(false)
        trackNodeTypes(bp)
        // Trigger initial validation after load
        onBlueprintChangedRef.current()
      })
      .catch((err) => {
        if (!cancelled) {
          toastWorkflowError(err, t("workflowUpdateFailed"), tError, t)
          router.replace("/workflows")
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingWorkflow(false)
      })
    return () => {
      cancelled = true
    }
  }, [userId, workflowId, router, t, tError, blueprintRef, trackNodeTypes, setWorkflow, currentSnapshot, cancelAutoSave])

  // Dirty state beforeunload guard
  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener("beforeunload", handler)
    return () => window.removeEventListener("beforeunload", handler)
  }, [isDirty])

  // Name, icon and description are edits like any other: they mark the
  // document unsaved and it saves itself after the pause.
  const changeName = useCallback(
    (name: string) => {
      if (!workflowRef.current) return
      setWorkflow((prev) => (prev ? { ...prev, name } : prev))
      noteEdit()
    },
    [setWorkflow, noteEdit],
  )

  const changeIcon = useCallback(
    (icon: string | null) => {
      if (!workflowRef.current) return
      setWorkflow((prev) => (prev ? { ...prev, icon } : prev))
      noteEdit()
    },
    [setWorkflow, noteEdit],
  )

  const changeDescription = useCallback(
    (description: string) => {
      if (!workflowRef.current) return
      setWorkflow((prev) => (prev ? { ...prev, description } : prev))
      noteEdit()
    },
    [setWorkflow, noteEdit],
  )

  const changeStatus = useCallback(
    async (status: "draft" | "active") => {
      const wf = workflowRef.current
      if (!wf || wf.status === status) return
      try {
        const updated = await workflowApi.update(wf.id, { status })
        // Only the status was sent: unsaved edits to the rest stay as they are.
        setWorkflow((local) => (local ? keepingLocalEdits(updated, local) : local))
        toast.success(t("statusChanged"))
      } catch (err) {
        toastWorkflowError(err, t("workflowUpdateFailed"), tError, t)
      }
    },
    [setWorkflow, t, tError],
  )

  /** Take a changed blueprint: mark unsaved, validate, and save after a pause. */
  const applyBlueprint = useCallback(
    (bp: WorkflowBlueprint) => {
      blueprintRef.current = bp
      trackNodeTypes(bp)
      // Overlay-only updates (validation badges, run status) serialize to
      // the stored text: they are not edits and are not validated again.
      // Neither is a pan or zoom.
      const changed = graphTextOf(bp) !== savedRef.current?.graphText
      noteEdit()
      if (changed) onBlueprintChangedRef.current()
    },
    [blueprintRef, trackNodeTypes, noteEdit],
  )

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

  // Keyboard shortcut: Cmd+S saves now
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault()
        if (!isSaving) void save()
      }
    }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [isSaving, save])

  const exportToFile = useCallback(async () => {
    const wf = workflowRef.current
    if (!wf) return
    try {
      const data = await workflowApi.export(wf.id)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = exportFileName(wf.name)
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      toastWorkflowError(err, t("workflowExportFailed"), tError, t)
    }
  }, [t, tError])

  /** Replace the blueprint being edited with an imported one. */
  const applyImport = useCallback(
    (blueprint: WorkflowBlueprint) => {
      blueprintRef.current = blueprint
      trackNodeTypes(blueprint)
      setWorkflow((prev) => (prev ? { ...prev, blueprint } : prev))
      // The canvas starts over from the imported blueprint. The import is an
      // edit: unsaved now, saved after the pause; a save still in flight
      // records what it sent, not this.
      setCanvasRevision((n) => n + 1)
      noteEdit()
      toast.success(t("workflowImported"))
    },
    [t, blueprintRef, trackNodeTypes, setWorkflow, noteEdit],
  )

  /**
   * Take the text of an exported file. An import replaces the whole canvas,
   * clears undo and is saved over the stored workflow a few seconds later,
   * so when there is anything to lose (steps on the canvas, or unsaved
   * edits) it waits in `pendingImport` for `confirmImport`.
   */
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
      const onCanvas = readCanvasRef.current?.() ?? blueprintRef.current
      if (onCanvas.nodes.length > 0 || hasUnsavedEdits()) {
        setPendingImport(blueprint)
        return
      }
      applyImport(blueprint)
    },
    [t, blueprintRef, hasUnsavedEdits, applyImport],
  )

  /** The user agreed to replace the canvas with the file picked. */
  const confirmImport = useCallback(() => {
    if (pendingImport) applyImport(pendingImport)
    setPendingImport(null)
  }, [pendingImport, applyImport])

  const cancelImport = useCallback(() => setPendingImport(null), [])

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
    const wf = workflowRef.current
    if (!wf) return
    setIsDuplicating(true)
    try {
      // The copy is made from what the server holds, and the page then
      // leaves: save first, and stay if that fails.
      if (isUnsaved()) {
        await runSave("manual")
        if (isUnsaved()) return
      }
      const duplicated = await workflowApi.duplicate(wf.id)
      toast.success(t("workflowDuplicated"))
      router.push(`/workflows/${duplicated.id}`)
    } catch (err) {
      toastWorkflowError(err, t("workflowDuplicateFailed"), tError, t)
    } finally {
      setIsDuplicating(false)
    }
  }, [router, t, tError, isUnsaved, runSave])

  const deleteWorkflow = useCallback(async () => {
    const wf = workflowRef.current
    if (!wf) return
    // A workflow about to be deleted is not saved again.
    suspendedRef.current = true
    cancelAutoSave()
    try {
      await workflowApi.delete(wf.id)
      toast.success(t("workflowDeleted"))
      router.replace("/workflows")
    } catch (err) {
      suspendedRef.current = false
      noteEdit()
      toastWorkflowError(err, t("workflowDeleteFailed"), tError, t)
    }
  }, [router, t, tError, cancelAutoSave, noteEdit])

  /**
   * Whether anything would be lost by leaving right now. Reads the canvas
   * first: an edit of the last instant has not been reported yet and the
   * `isDirty` a page rendered with does not know about it.
   */
  const hasUnsavedChanges = useCallback((): boolean => {
    if (suspendedRef.current) return false
    const canvas = readCanvasRef.current?.()
    if (canvas) {
      blueprintRef.current = withVariablesOf(canvas, blueprintRef.current)
      trackNodeTypes(blueprintRef.current)
      noteEdit()
    }
    return hasUnsavedEdits()
  }, [blueprintRef, trackNodeTypes, noteEdit, hasUnsavedEdits])

  /** Give up the unsaved edits: nothing is saved from here on (the page is being left). */
  const discardChanges = useCallback(() => {
    suspendedRef.current = true
    cancelAutoSave()
    setIsDirty(false)
  }, [cancelAutoSave])

  /**
   * A version is about to be restored on the server. From here until the
   * restored workflow is loaded, nothing this page holds may be saved: it
   * would overwrite the version being restored. Resolves once no save is
   * pending or in flight; call it before asking the server to restore.
   */
  const beginRestore = useCallback(async () => {
    suspendedRef.current = true
    cancelAutoSave()
    while (inFlightRef.current) await inFlightRef.current
  }, [cancelAutoSave])

  /** The restore did not happen: saving resumes, including what was pending. */
  const cancelRestore = useCallback(() => {
    suspendedRef.current = false
    noteEdit()
  }, [noteEdit])

  /** Reload the workflow from the server after a version restore. */
  const reloadAfterRestore = useCallback(() => {
    // The server now holds the restored version. Whatever is on the canvas
    // is superseded from this moment, not from when the reload answers: no
    // save may start, and one that somehow still answers is ignored.
    suspendedRef.current = true
    cancelAutoSave()
    epochRef.current += 1
    const epoch = epochRef.current
    workflowApi
      .get(workflowId)
      .then((data) => {
        if (epochRef.current !== epoch) return
        setWorkflow(data)
        const bp = data.blueprint ?? emptyBlueprint()
        blueprintRef.current = bp
        savedRef.current = currentSnapshot()
        trackNodeTypes(bp)
        // The canvas starts over from the restored blueprint.
        setCanvasRevision((n) => n + 1)
        setIsDirty(false)
        setLastSavedAt(new Date())
        suspendedRef.current = false
        onBlueprintChangedRef.current()
      })
      .catch((err) => {
        if (epochRef.current !== epoch) return
        // Restored on the server, but still the old graph here. Saving stays
        // off (it would undo the restore) and the edits it replaced are no
        // longer "unsaved changes" to warn about; the page has to be reloaded.
        setIsDirty(false)
        toastWorkflowError(err, t("versionRestoreReloadFailed"), tError, t)
      })
  }, [workflowId, t, tError, blueprintRef, trackNodeTypes, setWorkflow, currentSnapshot, cancelAutoSave])

  return {
    workflow,
    setWorkflow,
    isLoadingWorkflow,
    isSaving,
    isDuplicating,
    isDirty,
    lastSavedAt,
    canvasRevision,
    nodeTypeMap,
    changeName,
    changeIcon,
    changeDescription,
    changeStatus,
    save,
    saveBeforeRun,
    changeBlueprint,
    changeVariables,
    exportToFile,
    importFromFile,
    importFromText,
    hasPendingImport: pendingImport !== null,
    confirmImport,
    cancelImport,
    duplicate,
    deleteWorkflow,
    hasUnsavedChanges,
    discardChanges,
    beginRestore,
    cancelRestore,
    reloadAfterRestore,
  }
}
