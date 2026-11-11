import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { MutableRefObject } from "react"

import { ApiError } from "@/lib/api"

import { emptyBlueprint } from "@/components/workflows/editor-page/blueprint-derived"
import { useWorkflowDocument } from "@/components/workflows/editor-page/use-workflow-document"
import type { UserInfo } from "@/types/auth"
import type { WorkflowBlueprint, WorkflowResponse } from "@/types/workflow"

const api = vi.hoisted(() => ({
  get: vi.fn(),
  update: vi.fn(),
  export: vi.fn(),
  duplicate: vi.fn(),
  delete: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  workflowApi: api,
}))
vi.mock("next/navigation", () => ({ useRouter: () => router }))
// One translator for every render, as next-intl provides.
const translate = vi.hoisted(() => (key: string) => key)
vi.mock("next-intl", () => ({ useTranslations: () => translate }))
vi.mock("sonner", () => ({ toast }))

const user: UserInfo = {
  id: "u1",
  username: "ada",
  display_name: null,
  avatar: null,
  is_admin: false,
  onboarding_completed: true,
}

function canvasOf(prompt: string): WorkflowBlueprint {
  return {
    schema_version: 2,
    nodes: [
      { id: "start_1", type: "start", position: { x: 0, y: 0 }, data: { variables: [] } },
      { id: "llm_1", type: "llm", position: { x: 200, y: 0 }, data: { prompt_template: prompt } },
      { id: "end_1", type: "end", position: { x: 400, y: 0 }, data: { output_mapping: {} } },
    ],
    edges: [{ id: "e1", source: "start_1", target: "llm_1" }],
    viewport: { x: 0, y: 0, zoom: 1 },
  }
}

const noop = () => {}

type Doc = ReturnType<typeof useWorkflowDocument>

const REGION = { name: "region", type: "string" as const, default_value: "eu", description: "" }

function workflowWith(blueprint: WorkflowBlueprint, name = "Flow"): WorkflowResponse {
  return { id: "wf1", name, description: null, icon: null, status: "draft", blueprint } as WorkflowResponse
}

/** A promise resolved by hand, to hold a request in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

async function mountDocument(stored: WorkflowBlueprint, extra: { readCanvas?: () => WorkflowBlueprint | null } = {}) {
  api.get.mockResolvedValue(workflowWith(stored))
  api.update.mockImplementation(
    async (_id: string, body: { blueprint?: WorkflowBlueprint; name?: string }) =>
      workflowWith(body.blueprint ?? stored, body.name),
  )
  const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
  const onBlueprintChanged = vi.fn()
  const hook = renderHook(
    (props: { user: UserInfo | null }) =>
      useWorkflowDocument({
        workflowId: "wf1",
        userId: props.user?.id ?? null,
        blueprintRef,
        onBlueprintChanged,
        ...extra,
      }),
    { initialProps: { user } },
  )
  await waitFor(() => expect(hook.result.current.isLoadingWorkflow).toBe(false))
  return { ...hook, blueprintRef, onBlueprintChanged }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("useWorkflowDocument", () => {
  it("loads the workflow, holds its blueprint and starts clean", async () => {
    const stored = canvasOf("first")
    const { result, blueprintRef, onBlueprintChanged } = await mountDocument(stored)
    expect(result.current.workflow?.id).toBe("wf1")
    expect(blueprintRef.current).toEqual(stored)
    expect(result.current.isDirty).toBe(false)
    expect(onBlueprintChanged).toHaveBeenCalledTimes(1)
  })

  it("stays clean when the canvas reports what is stored, and turns unsaved on a real change", async () => {
    const { result } = await mountDocument(canvasOf("first"))
    act(() => result.current.changeBlueprint(canvasOf("first")))
    expect(result.current.isDirty).toBe(false)
    act(() => result.current.changeBlueprint(canvasOf("second")))
    expect(result.current.isDirty).toBe(true)
  })

  it("saves the blueprint with the schemas read off it, then is clean again", async () => {
    const { result } = await mountDocument(canvasOf("first"))
    act(() => result.current.changeBlueprint(canvasOf("second")))
    await act(() => result.current.save())
    expect(api.update).toHaveBeenCalledTimes(1)
    expect(api.update.mock.calls[0][1]).toMatchObject({
      name: "Flow",
      blueprint: canvasOf("second"),
      input_schema: null,
      output_schema: {},
    })
    expect(result.current.isDirty).toBe(false)
    expect(result.current.lastSavedAt).toBeInstanceOf(Date)
  })

  it("saves by itself five seconds after the last change", async () => {
    const { result } = await mountDocument(canvasOf("first"))
    vi.useFakeTimers()
    act(() => result.current.changeBlueprint(canvasOf("second")))
    act(() => result.current.changeBlueprint(canvasOf("third")))
    await act(() => vi.advanceTimersByTimeAsync(4900))
    expect(api.update).not.toHaveBeenCalled()
    await act(() => vi.advanceTimersByTimeAsync(200))
    expect(api.update).toHaveBeenCalledTimes(1)
    expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("third"))
  })

  // Validation answers by setting badges on the canvas nodes; the canvas
  // then reports the same blueprint again. That report was taken as an edit:
  // it restarted the five seconds and asked for validation again, which set
  // the badges again, so an unsaved workflow re-validated every ~2 seconds
  // and never saved itself.
  it("a canvas report that changes nothing neither delays the automatic save nor validates again", async () => {
    const { result, onBlueprintChanged } = await mountDocument(canvasOf("first"))
    vi.useFakeTimers()
    onBlueprintChanged.mockClear()
    act(() => result.current.changeBlueprint(canvasOf("second")))
    expect(onBlueprintChanged).toHaveBeenCalledTimes(1)
    for (let i = 0; i < 2; i++) {
      await act(() => vi.advanceTimersByTimeAsync(2300))
      // A validation badge changed node data, not the blueprint.
      act(() => result.current.changeBlueprint(canvasOf("second")))
    }
    expect(onBlueprintChanged).toHaveBeenCalledTimes(1)
    await act(() => vi.advanceTimersByTimeAsync(500))
    expect(api.update).toHaveBeenCalledTimes(1)
    expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("second"))
    expect(result.current.isDirty).toBe(false)
  })

  describe("loading", () => {
    // The server sends the blueprint to the owner only. The editor was
    // handed null and failed with "Cannot read properties of null".
    it("sends someone who may not edit the workflow back to the list, saying why", async () => {
      api.get.mockResolvedValue({ ...workflowWith(canvasOf("first")), user_id: "u-other", blueprint: null })
      const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
      const onBlueprintChanged = vi.fn()
      const { result } = renderHook(() =>
        useWorkflowDocument({ workflowId: "wf1", userId: user.id, blueprintRef, onBlueprintChanged }),
      )
      await waitFor(() => expect(result.current.isLoadingWorkflow).toBe(false))
      expect(result.current.workflow).toBeNull()
      expect(toast.error).toHaveBeenCalledWith("workflowOwnerOnly")
      expect(router.replace).toHaveBeenCalledWith("/workflows")
      expect(onBlueprintChanged).not.toHaveBeenCalled()
    })

    it("waits for a signed-in user", async () => {
      api.get.mockResolvedValue(workflowWith(canvasOf("first")))
      const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
      const { result, rerender } = renderHook(
        (props: { userId: string | null }) =>
          useWorkflowDocument({
            workflowId: "wf1",
            userId: props.userId,
            blueprintRef,
            onBlueprintChanged: noop,
          }),
        { initialProps: { userId: null as string | null } },
      )
      expect(api.get).not.toHaveBeenCalled()
      rerender({ userId: "u1" })
      await waitFor(() => expect(result.current.isLoadingWorkflow).toBe(false))
      expect(api.get).toHaveBeenCalledTimes(1)
    })

    // The auth context replaces the user object when /me answers and when
    // the session token is refreshed. The page used to reload the workflow
    // each time, remounting the canvas and dropping what was not saved yet.
    it("does not reload when the user object is replaced by one for the same user", async () => {
      const { result, rerender, blueprintRef } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("unsaved edit")))
      rerender({ user: { ...user, display_name: "Ada" } })
      rerender({ user: { ...user, has_agent: true } })
      expect(api.get).toHaveBeenCalledTimes(1)
      expect(result.current.isLoadingWorkflow).toBe(false)
      expect(blueprintRef.current).toEqual(canvasOf("unsaved edit"))
      expect(result.current.isDirty).toBe(true)
    })

    it("does not reload when the caller passes a new callback", async () => {
      api.get.mockResolvedValue(workflowWith(canvasOf("first")))
      const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
      const calls: string[] = []
      const { result, rerender } = renderHook(
        (props: { tag: string }) =>
          useWorkflowDocument({
            workflowId: "wf1",
            userId: "u1",
            blueprintRef,
            // A new function on every render, as an inline callback would be.
            onBlueprintChanged: () => calls.push(props.tag),
          }),
        { initialProps: { tag: "first" } },
      )
      await waitFor(() => expect(result.current.isLoadingWorkflow).toBe(false))
      rerender({ tag: "second" })
      rerender({ tag: "third" })
      expect(api.get).toHaveBeenCalledTimes(1)
      // And the callback used is the latest one.
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      expect(calls).toEqual(["first", "third"])
    })

    it("goes back to the list when the workflow cannot be loaded", async () => {
      api.get.mockRejectedValue(new Error("gone"))
      const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
      const { result } = renderHook(() =>
        useWorkflowDocument({
          workflowId: "wf1",
          userId: "u1",
          blueprintRef,
          onBlueprintChanged: noop,
        }),
      )
      await waitFor(() => expect(result.current.isLoadingWorkflow).toBe(false))
      expect(router.replace).toHaveBeenCalledWith("/workflows")
      expect(toast.error).toHaveBeenCalledWith("workflowUpdateFailed")
    })
  })

  describe("a save records what it sent", () => {
    // Save used to mark as saved whatever the document was when the answer
    // came back. An edit made while the request was in flight was then shown
    // as saved, and never sent unless another edit followed.
    it("an edit made while a save is in flight stays unsaved and is saved after the pause", async () => {
      const canvas = { current: canvasOf("second") }
      const { result } = await mountDocument(canvasOf("first"), { readCanvas: () => canvas.current })
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const put = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(put.promise)
      let saving!: Promise<void>
      act(() => {
        saving = result.current.save()
      })
      canvas.current = canvasOf("third")
      act(() => result.current.changeBlueprint(canvasOf("third")))
      await act(async () => {
        put.resolve(workflowWith(canvasOf("second")))
        await saving
      })
      expect(result.current.isDirty).toBe(true)
      expect(api.update).toHaveBeenCalledTimes(1)

      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(2)
      expect(api.update.mock.calls[1][1].blueprint).toEqual(canvasOf("third"))
      expect(result.current.isDirty).toBe(false)
    })

    it("an import that lands while a save is in flight is saved afterwards", async () => {
      const canvas = { current: canvasOf("second") }
      const { result, blueprintRef } = await mountDocument(canvasOf("first"), {
        readCanvas: () => canvas.current,
      })
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const put = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(put.promise)
      await act(() => vi.advanceTimersByTimeAsync(5000)) // the automatic save is now in flight
      expect(api.update).toHaveBeenCalledTimes(1)

      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("imported") })))
      act(() => result.current.confirmImport())
      canvas.current = canvasOf("imported")
      await act(async () => {
        put.resolve(workflowWith(canvasOf("second")))
      })
      expect(result.current.isDirty).toBe(true)
      // The canvas, started over from the import, makes its first report.
      act(() => result.current.changeBlueprint(canvasOf("imported")))
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(2)
      expect(api.update.mock.calls[1][1].blueprint).toEqual(canvasOf("imported"))
      expect(blueprintRef.current).toEqual(canvasOf("imported"))
      expect(result.current.isDirty).toBe(false)
    })

    it("a failed save leaves the document unsaved", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("second")))
      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(() => result.current.save())
      expect(result.current.isDirty).toBe(true)
      expect(toast.error).toHaveBeenLastCalledWith("workflowUpdateFailed")
    })

    it("going back to the stored state by hand is not unsaved, and saves nothing", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      act(() => result.current.changeBlueprint(canvasOf("first")))
      expect(result.current.isDirty).toBe(false)
      await act(() => vi.advanceTimersByTimeAsync(6000))
      expect(api.update).not.toHaveBeenCalled()
    })
  })

  describe("name, description and icon are edits like any other", () => {
    // The automatic save ran the save function captured at the last canvas
    // change, which carried the name of that moment: move a step, rename
    // within five seconds, and the save sent the old name and put it back
    // in the toolbar. A rename on its own never saved itself at all.
    it("a rename made after the last canvas edit is what the automatic save sends", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      act(() => result.current.changeName("Renamed"))
      expect(result.current.workflow?.name).toBe("Renamed")
      await act(() => vi.advanceTimersByTimeAsync(5100))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(api.update.mock.calls[0][1].name).toBe("Renamed")
      expect(result.current.workflow?.name).toBe("Renamed")
      expect(result.current.isDirty).toBe(false)
    })

    it.each([
      ["a rename", (doc: Doc) => doc.changeName("Renamed"), { name: "Renamed" }],
      ["a new description", (doc: Doc) => doc.changeDescription("what it does"), { description: "what it does" }],
      ["a new icon", (doc: Doc) => doc.changeIcon("🧪"), { icon: "🧪" }],
    ])("%s on its own marks the workflow unsaved and saves itself after the pause", async (_what, edit, sent) => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => edit(result.current))
      expect(result.current.isDirty).toBe(true)
      await act(() => vi.advanceTimersByTimeAsync(4900))
      expect(api.update).not.toHaveBeenCalled()
      await act(() => vi.advanceTimersByTimeAsync(200))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(api.update.mock.calls[0][1]).toMatchObject(sent)
      expect(result.current.isDirty).toBe(false)
    })

    it("a rename made while a save is in flight is kept and saved next", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const put = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(put.promise)
      let saving!: Promise<void>
      act(() => {
        saving = result.current.save()
      })
      act(() => result.current.changeName("Renamed"))
      await act(async () => {
        put.resolve(workflowWith(canvasOf("second"), "Flow"))
        await saving
      })
      // The answer carries the old name; the toolbar keeps the new one.
      expect(result.current.workflow?.name).toBe("Renamed")
      expect(result.current.isDirty).toBe(true)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update.mock.calls[1][1].name).toBe("Renamed")
      expect(result.current.isDirty).toBe(false)
    })

    it("a status change does not put back a name being edited", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeName("Renamed"))
      api.update.mockResolvedValueOnce({ ...workflowWith(canvasOf("first"), "Flow"), status: "active" })
      await act(() => result.current.changeStatus("active"))
      expect(result.current.workflow?.status).toBe("active")
      expect(result.current.workflow?.name).toBe("Renamed")
      expect(result.current.isDirty).toBe(true)
    })
  })

  describe("one way of saving", () => {
    // The Save button did not cancel the pending automatic save (Cmd+S did),
    // and a manual save started while an automatic one was in flight was
    // treated as automatic: no "Saving...", errors swallowed.
    it("a manual save takes the place of the pending automatic one", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      await act(() => result.current.save())
      expect(api.update).toHaveBeenCalledTimes(1)
      await act(() => vi.advanceTimersByTimeAsync(10000))
      expect(api.update).toHaveBeenCalledTimes(1)
    })

    it("a manual save during an automatic one shows Saving, waits its turn and reports its own failure", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const auto = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(auto.promise)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(result.current.isSaving).toBe(false) // automatic saves are silent

      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      let manual!: Promise<void>
      act(() => {
        manual = result.current.save()
      })
      expect(result.current.isSaving).toBe(true)
      expect(api.update).toHaveBeenCalledTimes(1) // one request at a time
      await act(async () => {
        auto.resolve(workflowWith(canvasOf("second")))
        await manual
      })
      expect(api.update).toHaveBeenCalledTimes(2)
      expect(result.current.isSaving).toBe(false)
      expect(toast.error).toHaveBeenLastCalledWith("workflowUpdateFailed")
    })

    it("an automatic save that fails stays silent", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(toast.error).not.toHaveBeenCalled()
      expect(result.current.isDirty).toBe(true)
    })

    it("duplicating saves unsaved edits first, and stays when that save fails", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      api.duplicate.mockResolvedValue({ id: "wf2" })
      act(() => result.current.changeBlueprint(canvasOf("second")))
      await act(() => result.current.duplicate())
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("second"))
      expect(api.duplicate).toHaveBeenCalledTimes(1)
      expect(router.push).toHaveBeenCalledWith("/workflows/wf2")

      act(() => result.current.changeBlueprint(canvasOf("third")))
      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(() => result.current.duplicate())
      expect(api.duplicate).toHaveBeenCalledTimes(1)
      expect(router.push).toHaveBeenCalledTimes(1)
    })

    it("knows about an edit the canvas has not reported yet when asked whether leaving loses anything", async () => {
      const canvas = { current: canvasOf("first") }
      const { result } = await mountDocument(canvasOf("first"), { readCanvas: () => canvas.current })
      expect(result.current.hasUnsavedChanges()).toBe(false)
      canvas.current = canvasOf("typed an instant ago")
      expect(result.current.isDirty).toBe(false) // the page has not heard of it
      let unsaved = false
      act(() => {
        unsaved = result.current.hasUnsavedChanges()
      })
      expect(unsaved).toBe(true)
      expect(result.current.isDirty).toBe(true)
      await act(() => result.current.save())
      expect(result.current.hasUnsavedChanges()).toBe(false)
      // Once the edits are given up, leaving loses nothing more.
      canvas.current = canvasOf("another edit")
      act(() => result.current.discardChanges())
      expect(result.current.hasUnsavedChanges()).toBe(false)
    })

    it("nothing is saved after the edits were discarded or the workflow deleted", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      act(() => result.current.discardChanges())
      expect(result.current.isDirty).toBe(false)
      await act(() => vi.advanceTimersByTimeAsync(10000))
      await act(() => result.current.save())
      expect(api.update).not.toHaveBeenCalled()
    })
  })

  describe("failures say why when the server gave a reason", () => {
    // These actions used to show one fixed sentence whatever the server
    // answered. A failure that carries an error code now shows its message;
    // one without (network down) keeps the fixed sentence.
    const refused = () =>
      new ApiError(403, "Only the workflow's author can do this", "workflow_author_only", {})

    it("status change", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      api.update.mockRejectedValueOnce(refused())
      await act(() => result.current.changeStatus("active"))
      expect(toast.error).toHaveBeenLastCalledWith(
        "Only the workflow's author can do this",
        expect.objectContaining({ id: "workflow_author_only" }),
      )
      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(() => result.current.changeStatus("active"))
      expect(toast.error).toHaveBeenLastCalledWith("workflowUpdateFailed")
    })

    it("delete", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      api.delete.mockRejectedValueOnce(refused())
      await act(() => result.current.deleteWorkflow())
      expect(toast.error).toHaveBeenLastCalledWith(
        "Only the workflow's author can do this",
        expect.objectContaining({ id: "workflow_author_only" }),
      )
      expect(router.replace).not.toHaveBeenCalled()
      api.delete.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(() => result.current.deleteWorkflow())
      expect(toast.error).toHaveBeenLastCalledWith("workflowDeleteFailed")
    })

    it("export", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      api.export.mockRejectedValueOnce(refused())
      await act(() => result.current.exportToFile())
      expect(toast.error).toHaveBeenLastCalledWith(
        "Only the workflow's author can do this",
        expect.objectContaining({ id: "workflow_author_only" }),
      )
    })

    it("loading", async () => {
      api.get.mockRejectedValue(new ApiError(404, "Workflow not found", "workflow_not_found", {}))
      const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
      const { result } = renderHook(() =>
        useWorkflowDocument({ workflowId: "wf1", userId: "u1", blueprintRef, onBlueprintChanged: noop }),
      )
      await waitFor(() => expect(result.current.isLoadingWorkflow).toBe(false))
      expect(toast.error).toHaveBeenLastCalledWith(
        "Workflow not found",
        expect.objectContaining({ id: "workflow_not_found" }),
      )
      expect(router.replace).toHaveBeenCalledWith("/workflows")
    })
  })

  describe("node types for the panels that name steps", () => {
    // The page used to compute this map once, before the workflow had
    // loaded, so it stayed empty: the run panel listed raw node ids and
    // never showed its "n/m nodes" progress.
    it("is filled when the workflow loads", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      expect(result.current.nodeTypeMap).toEqual({ start_1: "start", llm_1: "llm", end_1: "end" })
    })

    it("follows nodes added on the canvas and keeps its identity otherwise", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      const before = result.current.nodeTypeMap
      act(() => result.current.changeBlueprint(canvasOf("only the prompt changed")))
      expect(result.current.nodeTypeMap).toBe(before)

      const grown = canvasOf("first")
      grown.nodes.push({ id: "agent_1", type: "agent", position: { x: 0, y: 0 }, data: {} })
      act(() => result.current.changeBlueprint(grown))
      expect(result.current.nodeTypeMap).toEqual({ ...before, agent_1: "agent" })
    })

    it("follows an import", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      const imported = canvasOf("x")
      imported.nodes = imported.nodes.filter((n) => n.id !== "llm_1")
      act(() => result.current.importFromText(JSON.stringify({ blueprint: imported })))
      act(() => result.current.confirmImport())
      expect(result.current.nodeTypeMap).toEqual({ start_1: "start", end_1: "end" })
    })
  })

  describe("saving what is on the canvas", () => {
    // The canvas reports a change 300 ms after it happened. Save used to
    // send the last report, so an edit made just before Save was left out.
    it("sends the canvas as it is, not the last report", async () => {
      let onCanvas = canvasOf("first")
      const { result, blueprintRef } = await mountDocument(canvasOf("first"), {
        readCanvas: () => onCanvas,
      })
      act(() => result.current.changeBlueprint(canvasOf("reported")))
      onCanvas = canvasOf("typed just now")
      await act(() => result.current.save())
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("typed just now"))
      expect(blueprintRef.current).toEqual(canvasOf("typed just now"))
      expect(result.current.isDirty).toBe(false)

      // The canvas's own report of the same state arrives later: nothing to save.
      act(() => result.current.changeBlueprint(canvasOf("typed just now")))
      expect(result.current.isDirty).toBe(false)
    })

    it("keeps the workflow variables on that path too", async () => {
      const { result } = await mountDocument(
        { ...canvasOf("first"), variables: [REGION] },
        { readCanvas: () => canvasOf("typed just now") },
      )
      await act(() => result.current.save())
      expect(api.update.mock.calls[0][1].blueprint).toEqual({
        ...canvasOf("typed just now"),
        variables: [REGION],
      })
    })

    it("saves the last report when there is no canvas to read", async () => {
      const { result } = await mountDocument(canvasOf("first"), { readCanvas: () => null })
      act(() => result.current.changeBlueprint(canvasOf("reported")))
      await act(() => result.current.save())
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("reported"))
    })
  })

  describe("replacing the whole blueprint", () => {
    // The canvas keeps its own state once mounted. Import and version
    // restore used to change the blueprint behind it and leave the canvas
    // showing (and later saving) the old graph.
    it("a version restore reloads the workflow and tells the canvas to start over", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("third"))
      act(() => result.current.changeBlueprint(canvasOf("unsaved edit")))
      const before = result.current.canvasRevision
      api.get.mockResolvedValue(workflowWith(canvasOf("restored")))
      act(() => result.current.reloadAfterRestore())
      await waitFor(() => expect(result.current.canvasRevision).toBe(before + 1))
      expect(blueprintRef.current).toEqual(canvasOf("restored"))
      expect(result.current.workflow?.blueprint).toEqual(canvasOf("restored"))
      expect(result.current.isDirty).toBe(false)

      // The remounted canvas reports the restored graph: still nothing to save.
      act(() => result.current.changeBlueprint(canvasOf("restored")))
      expect(result.current.isDirty).toBe(false)
    })

    // The pending automatic save was cancelled only once the reload had
    // answered, so it could fire during the restore request or the reload,
    // and a save already in flight landed after the restore: the canvas
    // showed the restored graph, the toolbar "Saved", the server the old one.
    it("nothing is saved between asking to restore and the restored workflow being loaded", async () => {
      const canvas = { current: canvasOf("edited") }
      const { result, blueprintRef } = await mountDocument(canvasOf("first"), {
        readCanvas: () => canvas.current,
      })
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      await act(() => vi.advanceTimersByTimeAsync(4900))

      await act(() => result.current.beginRestore())
      // The restore request is on its way; the five seconds run out meanwhile.
      await act(() => vi.advanceTimersByTimeAsync(1000))
      expect(api.update).not.toHaveBeenCalled()

      const reload = deferred<WorkflowResponse>()
      api.get.mockReturnValueOnce(reload.promise)
      act(() => result.current.reloadAfterRestore())
      await act(() => vi.advanceTimersByTimeAsync(10000))
      await act(() => result.current.save()) // not even by hand
      expect(api.update).not.toHaveBeenCalled()

      await act(async () => {
        reload.resolve(workflowWith(canvasOf("restored")))
      })
      expect(blueprintRef.current).toEqual(canvasOf("restored"))
      expect(result.current.isDirty).toBe(false)
      await act(() => vi.advanceTimersByTimeAsync(10000))
      expect(api.update).not.toHaveBeenCalled()
    })

    it("the reload alone already stops the pending automatic save", async () => {
      const { result } = await mountDocument(canvasOf("first"), { readCanvas: () => canvasOf("edited") })
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      await act(() => vi.advanceTimersByTimeAsync(4900))
      const reload = deferred<WorkflowResponse>()
      api.get.mockReturnValueOnce(reload.promise)
      act(() => result.current.reloadAfterRestore())
      await act(() => vi.advanceTimersByTimeAsync(200))
      expect(api.update).not.toHaveBeenCalled()
    })

    it("a restore waits for the save in flight before it asks the server", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      const put = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(put.promise)
      let saving!: Promise<void>
      act(() => {
        saving = result.current.save()
      })
      let mayRestore = false
      let begun!: Promise<void>
      act(() => {
        begun = result.current.beginRestore().then(() => {
          mayRestore = true
        })
      })
      await act(async () => {
        await Promise.resolve()
      })
      expect(mayRestore).toBe(false)
      await act(async () => {
        put.resolve(workflowWith(canvasOf("edited")))
        await saving
        await begun
      })
      expect(mayRestore).toBe(true)
    })

    it("an answer to a save from before the restore is ignored", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      const put = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(put.promise)
      let saving!: Promise<void>
      act(() => {
        saving = result.current.save()
      })
      api.get.mockResolvedValue(workflowWith(canvasOf("restored")))
      act(() => result.current.reloadAfterRestore())
      await waitFor(() => expect(blueprintRef.current).toEqual(canvasOf("restored")))
      await act(async () => {
        put.resolve(workflowWith(canvasOf("edited"), "Other name"))
        await saving
      })
      expect(result.current.workflow?.name).toBe("Flow")
      expect(result.current.workflow?.blueprint).toEqual(canvasOf("restored"))
      // "Saved" still means the restored graph: reporting it is not an edit.
      act(() => result.current.changeBlueprint(canvasOf("restored")))
      expect(result.current.isDirty).toBe(false)
    })

    it("when the restore fails, what was pending is saved after the pause again", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      await act(() => result.current.beginRestore())
      await act(() => vi.advanceTimersByTimeAsync(6000))
      expect(api.update).not.toHaveBeenCalled()
      act(() => result.current.cancelRestore())
      expect(result.current.isDirty).toBe(true)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("edited"))
    })

    it("when the restored workflow cannot be loaded, saving stays off and the page says to reload", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("edited")))
      api.get.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      await act(async () => {
        result.current.reloadAfterRestore()
      })
      expect(toast.error).toHaveBeenLastCalledWith("versionRestoreReloadFailed")
      expect(result.current.isDirty).toBe(false)
      await act(() => vi.advanceTimersByTimeAsync(10000))
      await act(() => result.current.save())
      expect(api.update).not.toHaveBeenCalled()
    })

    it("a restore drops the automatic save of what it replaced", async () => {
      const { result } = await mountDocument(canvasOf("third"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("unsaved edit")))
      api.get.mockResolvedValue(workflowWith(canvasOf("restored")))
      act(() => result.current.reloadAfterRestore())
      await act(() => vi.advanceTimersByTimeAsync(6000))
      expect(api.update).not.toHaveBeenCalled()
    })

    it("an imported file replaces the blueprint and tells the canvas to start over", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      const before = result.current.canvasRevision
      const imported = { ...canvasOf("imported"), variables: [REGION] }
      act(() => result.current.importFromText(JSON.stringify({ format: "x", workflow: { blueprint: imported } })))
      act(() => result.current.confirmImport())
      expect(result.current.canvasRevision).toBe(before + 1)
      expect(blueprintRef.current).toEqual(imported)
      expect(result.current.workflow?.blueprint).toEqual(imported)
      expect(result.current.isDirty).toBe(true)
      expect(toast.success).toHaveBeenCalledWith("workflowImported")

      // The canvas reports the imported graph; the file's variables stay.
      act(() => result.current.changeBlueprint(canvasOf("imported")))
      expect(blueprintRef.current.variables).toEqual([REGION])
    })

    // Import replaces the canvas, clears undo and is saved over the stored
    // workflow a few seconds later: with something on the canvas it asks first.
    it("asks before replacing a canvas that has steps, and does nothing until confirmed", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      const before = result.current.canvasRevision
      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("imported") })))
      expect(result.current.hasPendingImport).toBe(true)
      expect(blueprintRef.current).toEqual(canvasOf("first"))
      expect(result.current.canvasRevision).toBe(before)
      expect(result.current.isDirty).toBe(false)
      expect(toast.success).not.toHaveBeenCalled()

      act(() => result.current.confirmImport())
      expect(result.current.hasPendingImport).toBe(false)
      expect(blueprintRef.current).toEqual(canvasOf("imported"))
      expect(result.current.canvasRevision).toBe(before + 1)
      expect(result.current.isDirty).toBe(true)
    })

    it("leaves everything as it was when the question is answered with Cancel", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      const before = result.current.canvasRevision
      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("imported") })))
      act(() => result.current.cancelImport())
      expect(result.current.hasPendingImport).toBe(false)
      expect(blueprintRef.current).toEqual(canvasOf("first"))
      expect(result.current.canvasRevision).toBe(before)
      // Confirming later does nothing: there is no file waiting any more.
      act(() => result.current.confirmImport())
      expect(blueprintRef.current).toEqual(canvasOf("first"))
      await act(() => vi.advanceTimersByTimeAsync(10000))
      expect(api.update).not.toHaveBeenCalled()
    })

    it("asks by what is on the canvas now, not by its last report", async () => {
      const empty = emptyBlueprint()
      // Stored empty, but a step was just dropped and not reported yet.
      const { result } = await mountDocument(empty, { readCanvas: () => canvasOf("just dropped") })
      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("imported") })))
      expect(result.current.hasPendingImport).toBe(true)
    })

    it("imports straight away into an empty, saved canvas", async () => {
      const { result, blueprintRef } = await mountDocument(emptyBlueprint())
      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("imported") })))
      expect(result.current.hasPendingImport).toBe(false)
      expect(blueprintRef.current).toEqual(canvasOf("imported"))
      expect(toast.success).toHaveBeenCalledWith("workflowImported")
    })

    it("accepts a bare { blueprint } file", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      act(() => result.current.importFromText(JSON.stringify({ blueprint: canvasOf("bare") })))
      act(() => result.current.confirmImport())
      expect(blueprintRef.current).toEqual(canvasOf("bare"))
    })

    it.each([
      ["not JSON", "{oops", "workflowImportFailed"],
      ["JSON without a blueprint", JSON.stringify({ hello: 1 }), "importInvalidFormat"],
      ["a blueprint without node and edge lists", JSON.stringify({ blueprint: { nodes: "x" } }), "importInvalidBlueprint"],
    ])("refuses %s and says so, leaving the canvas alone", async (_what, text, message) => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      const before = result.current.canvasRevision
      act(() => result.current.importFromText(text))
      expect(toast.error).toHaveBeenCalledWith(message)
      expect(toast.success).not.toHaveBeenCalled()
      expect(result.current.hasPendingImport).toBe(false)
      expect(result.current.canvasRevision).toBe(before)
      expect(blueprintRef.current).toEqual(canvasOf("first"))
      expect(result.current.isDirty).toBe(false)
    })
  })

  describe("workflow variables", () => {
    // The canvas serializes nodes, edges and the viewport only. A workflow
    // that has variables used to lose them at the first canvas report.
    const stored = { ...canvasOf("first"), variables: [REGION] }

    it("opening a workflow that has variables does not mark it unsaved", async () => {
      const { result, blueprintRef } = await mountDocument(stored)
      act(() => result.current.changeBlueprint(canvasOf("first")))
      expect(result.current.isDirty).toBe(false)
      expect(blueprintRef.current.variables).toEqual([REGION])
    })

    it("a canvas edit keeps the variables, and they are saved with it", async () => {
      const { result, blueprintRef } = await mountDocument(stored)
      act(() => result.current.changeBlueprint(canvasOf("second")))
      expect(blueprintRef.current.variables).toEqual([REGION])
      await act(() => result.current.save())
      expect(api.update.mock.calls[0][1].blueprint).toEqual({ ...canvasOf("second"), variables: [REGION] })
    })

    it("variables added in their panel survive the next canvas edit", async () => {
      const { result, blueprintRef } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeVariables([REGION]))
      expect(result.current.isDirty).toBe(true)
      act(() => result.current.changeBlueprint(canvasOf("second")))
      expect(blueprintRef.current.variables).toEqual([REGION])
      expect(blueprintRef.current.nodes[1].data.prompt_template).toBe("second")
    })
  })

  describe("pan and zoom", () => {
    const panned = (prompt: string): WorkflowBlueprint => ({
      ...canvasOf(prompt),
      viewport: { x: -80, y: 32, zoom: 0.5 },
    })

    beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))

    it("a pan alone is saved after the pause, without the document ever showing as unsaved", async () => {
      const { result, onBlueprintChanged } = await mountDocument(canvasOf("first"))
      onBlueprintChanged.mockClear()
      act(() => result.current.changeBlueprint(panned("first")))
      expect(result.current.isDirty).toBe(false)
      expect(result.current.hasUnsavedChanges()).toBe(false)
      // Nothing about the steps changed: no reason to validate again.
      expect(onBlueprintChanged).not.toHaveBeenCalled()
      expect(api.update).not.toHaveBeenCalled()

      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(api.update.mock.calls[0][1].blueprint.viewport).toEqual({ x: -80, y: 32, zoom: 0.5 })
      expect(result.current.isDirty).toBe(false)

      // Saved: the same viewport reported again sends nothing.
      act(() => result.current.changeBlueprint(panned("first")))
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)
    })

    it("an edit is unsaved as before, and is saved together with the pan", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(panned("second")))
      expect(result.current.isDirty).toBe(true)
      expect(result.current.hasUnsavedChanges()).toBe(true)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update.mock.calls[0][1].blueprint).toEqual(panned("second"))
      expect(result.current.isDirty).toBe(false)
    })

    it("a pan the canvas has not reported yet does not hold up leaving", async () => {
      const readCanvas = vi.fn(() => panned("first"))
      const { result } = await mountDocument(canvasOf("first"), { readCanvas })
      let unsaved = true
      act(() => {
        unsaved = result.current.hasUnsavedChanges()
      })
      expect(unsaved).toBe(false)
      expect(result.current.isDirty).toBe(false)
      // It is still saved after the pause.
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update.mock.calls[0][1].blueprint.viewport).toEqual({ x: -80, y: 32, zoom: 0.5 })
    })
  })

  // Run executes what the server holds. Clicked within the automatic save's
  // five seconds, it ran the graph as it was before the last edits.
  describe("saving before a run", () => {
    it("saves an edit that was still waiting for the automatic save, and says the run may start", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("second")))
      let ready: boolean | undefined
      await act(async () => {
        ready = await result.current.saveBeforeRun()
      })
      expect(ready).toBe(true)
      expect(api.update).toHaveBeenCalledTimes(1)
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("second"))
      expect(result.current.isDirty).toBe(false)
    })

    it("saves an edit the canvas has not reported yet", async () => {
      const readCanvas = vi.fn(() => canvasOf("typed a moment ago"))
      const { result } = await mountDocument(canvasOf("first"), { readCanvas })
      let ready: boolean | undefined
      await act(async () => {
        ready = await result.current.saveBeforeRun()
      })
      expect(ready).toBe(true)
      expect(api.update.mock.calls[0][1].blueprint).toEqual(canvasOf("typed a moment ago"))
    })

    it("says the run must not start when the save fails, and shows why", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      act(() => result.current.changeBlueprint(canvasOf("second")))
      api.update.mockRejectedValueOnce(new TypeError("Failed to fetch"))
      let ready: boolean | undefined
      await act(async () => {
        ready = await result.current.saveBeforeRun()
      })
      expect(ready).toBe(false)
      expect(toast.error).toHaveBeenLastCalledWith("workflowUpdateFailed")
      expect(result.current.isDirty).toBe(true)
    })

    it("sends nothing when everything is saved", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      let ready: boolean | undefined
      await act(async () => {
        ready = await result.current.saveBeforeRun()
      })
      expect(ready).toBe(true)
      expect(api.update).not.toHaveBeenCalled()
    })

    it("waits for a save already on its way and does not send the same thing twice", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const auto = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(auto.promise)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      expect(api.update).toHaveBeenCalledTimes(1)

      let settled = false
      let pending!: Promise<boolean>
      act(() => {
        pending = result.current.saveBeforeRun()
        void pending.then(() => {
          settled = true
        })
      })
      await act(() => vi.advanceTimersByTimeAsync(50))
      expect(settled).toBe(false)
      let ready: boolean | undefined
      await act(async () => {
        auto.resolve(workflowWith(canvasOf("second")))
        ready = await pending
      })
      expect(ready).toBe(true)
      expect(api.update).toHaveBeenCalledTimes(1)
    })

    it("when the save on its way fails, saves again itself and reports that outcome", async () => {
      const { result } = await mountDocument(canvasOf("first"))
      vi.useFakeTimers()
      act(() => result.current.changeBlueprint(canvasOf("second")))
      const auto = deferred<WorkflowResponse>()
      api.update.mockReturnValueOnce(auto.promise)
      await act(() => vi.advanceTimersByTimeAsync(5000))
      let ready: boolean | undefined
      await act(async () => {
        const pending = result.current.saveBeforeRun()
        auto.reject(new TypeError("Failed to fetch"))
        ready = await pending
      })
      expect(ready).toBe(true)
      expect(api.update).toHaveBeenCalledTimes(2)
      expect(api.update.mock.calls[1][1].blueprint).toEqual(canvasOf("second"))
    })
  })
})
