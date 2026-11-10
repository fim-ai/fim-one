import { describe, expect, it } from "vitest"

import { ApiError } from "@/lib/api"
import {
  refusedInputOf,
  refusedRowOf,
  runInputsOf,
} from "@/components/workflows/editor-page/run-inputs"
import { startChecksInputs } from "@/components/workflows/editor-page/blueprint-derived"
import { blankWorkflowBlueprint, defaultNodeData } from "@/lib/workflow-blueprint"
import type { WorkflowBlueprint } from "@/types/workflow"

const VARIABLES = [
  { name: "topic", type: "string", required: true },
  { name: "count", type: "number", default_value: "3" },
  { name: "strict", type: "boolean" },
  { name: "options", type: "object" },
  { name: "tags", type: "array" },
]

describe("runInputsOf", () => {
  it("sends what was typed as text and leaves empty fields out when the server checks inputs", () => {
    const { inputs, effectiveStrings } = runInputsOf(
      VARIABLES,
      { topic: "cats", count: "12", strict: "false", options: '{"a": 1}', tags: "  " },
      true,
    )
    // The server converts: "12" stays text here.
    expect(inputs).toEqual({ topic: "cats", count: "12", strict: "false", options: '{"a": 1}' })
    expect(effectiveStrings).toMatchObject({ topic: "cats", count: "12", tags: "  " })
  })

  it("does not fill in a default itself when the server checks inputs", () => {
    expect(runInputsOf(VARIABLES, {}, true).inputs).toEqual({})
  })

  it("converts as before when the server does not check inputs", () => {
    expect(
      runInputsOf(VARIABLES, { topic: "cats", count: "12", strict: "true", options: "{}" }, false).inputs,
    ).toEqual({ topic: "cats", count: 12, strict: true, options: "{}", tags: "" })
    // Empty fields: the default, then 0 / false / "".
    expect(runInputsOf(VARIABLES, {}, false).inputs).toEqual({
      topic: "",
      count: 3,
      strict: false,
      options: "",
      tags: "",
    })
    expect(runInputsOf([{ name: "n", type: "number" }], { n: "abc" }, false).inputs.n).toBeNaN()
  })
})

describe("input refusals", () => {
  const refusal = (code: string, args: Record<string, unknown>) => new ApiError(422, "x", code, args)

  it("names the input a refused run is about", () => {
    expect(refusedInputOf(refusal("workflow_input_required", { input: "topic" }))).toBe("topic")
    expect(refusedInputOf(refusal("workflow_input_invalid", { input: "count", expected: "a number" }))).toBe("count")
    expect(refusedInputOf(refusal("workflow_already_running", { input: "count" }))).toBeNull()
    expect(refusedInputOf(refusal("workflow_input_invalid", {}))).toBeNull()
    expect(refusedInputOf(new Error("network"))).toBeNull()
  })

  it("gives the batch row of a refusal, counted from 1", () => {
    expect(refusedRowOf(refusal("workflow_input_invalid", { input: "count", index: 0 }))).toBe(1)
    expect(refusedRowOf(refusal("workflow_input_required", { input: "topic", index: 4 }))).toBe(5)
    expect(refusedRowOf(refusal("workflow_input_required", { input: "topic" }))).toBeNull()
    expect(refusedRowOf(refusal("workflow_rate_limited", { index: 2 }))).toBeNull()
  })
})

describe("the Start step's input check", () => {
  const withStart = (data: Record<string, unknown>): WorkflowBlueprint => ({
    nodes: [{ id: "s", type: "start", position: { x: 0, y: 0 }, data }],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
  })

  it("is on for a new Start step and a new blank workflow", () => {
    expect(defaultNodeData("start")).toEqual({ variables: [], validate_inputs: true })
    const blank = blankWorkflowBlueprint()
    expect(blank.nodes.map((n) => n.type)).toEqual(["start", "end"])
    expect(blank.nodes[0].data.validate_inputs).toBe(true)
    expect(startChecksInputs(blank as WorkflowBlueprint)).toBe(true)
  })

  it("stays off for a Start step saved without the flag", () => {
    expect(startChecksInputs(withStart({ variables: [] }))).toBe(false)
    expect(startChecksInputs(withStart({ variables: [], validate_inputs: false }))).toBe(false)
    expect(startChecksInputs({ nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } })).toBe(false)
  })
})
