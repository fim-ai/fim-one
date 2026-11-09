import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, renderHook, waitFor } from "@testing-library/react"
import type { MutableRefObject } from "react"

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

const REGION = { name: "region", type: "string" as const, default_value: "eu", description: "" }

function workflowWith(blueprint: WorkflowBlueprint): WorkflowResponse {
  return { id: "wf1", name: "Flow", description: null, icon: null, status: "draft", blueprint } as WorkflowResponse
}

async function mountDocument(stored: WorkflowBlueprint, extra: { readCanvas?: () => WorkflowBlueprint | null } = {}) {
  api.get.mockResolvedValue(workflowWith(stored))
  api.update.mockImplementation(async (_id: string, body: { blueprint?: WorkflowBlueprint }) =>
    workflowWith(body.blueprint ?? stored),
  )
  const blueprintRef: MutableRefObject<WorkflowBlueprint> = { current: emptyBlueprint() }
  const onBlueprintChanged = vi.fn()
  const hook = renderHook(
    (props: { user: UserInfo | null }) =>
      useWorkflowDocument({
        workflowId: "wf1",
        user: props.user,
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
})
