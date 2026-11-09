import { describe, expect, it } from "vitest"

import {
  blueprintOfImport,
  emptyBlueprint,
  exportFileName,
  inputSchemaOf,
  nodeLabelOf,
  nodeTypeMapOf,
  outputSchemaOf,
  startVariablesOf,
} from "@/components/workflows/editor-page/blueprint-derived"
import type { WorkflowBlueprint } from "@/types/workflow"

const blueprint: WorkflowBlueprint = {
  schema_version: 2,
  nodes: [
    {
      id: "start_1",
      type: "start",
      position: { x: 0, y: 0 },
      data: {
        variables: [
          { name: "topic", type: "string", required: true },
          { name: "count", type: "number" },
        ],
      },
    },
    { id: "llm_1", type: "llm", position: { x: 0, y: 0 }, data: { label: "Draft" } },
    {
      id: "end_1",
      type: "end",
      position: { x: 0, y: 0 },
      data: { output_mapping: { text: "{{llm_1.out}}", n: "{{start_1.count}}" } },
    },
  ],
  edges: [],
  viewport: { x: 0, y: 0, zoom: 1 },
}

describe("schemas saved with a workflow", () => {
  it("input schema has one entry per Start variable; required defaults to false", () => {
    expect(inputSchemaOf(blueprint)).toEqual({
      topic: { type: "string", required: true },
      count: { type: "number", required: false },
    })
  })

  it("input schema is null without Start variables", () => {
    expect(inputSchemaOf(emptyBlueprint())).toBeNull()
    expect(
      inputSchemaOf({
        ...blueprint,
        nodes: [{ id: "s", type: "start", position: { x: 0, y: 0 }, data: { variables: [] } }],
      }),
    ).toBeNull()
  })

  it("output schema has one string entry per End mapping key", () => {
    expect(outputSchemaOf(blueprint)).toEqual({ text: { type: "string" }, n: { type: "string" } })
  })

  it("output schema is an empty object for an End without mappings, null without an End", () => {
    expect(
      outputSchemaOf({
        ...blueprint,
        nodes: [{ id: "e", type: "end", position: { x: 0, y: 0 }, data: { output_mapping: {} } }],
      }),
    ).toEqual({})
    expect(outputSchemaOf(emptyBlueprint())).toBeNull()
  })
})

describe("lookups", () => {
  it("lists the Start variables", () => {
    expect(startVariablesOf(blueprint).map((v) => v.name)).toEqual(["topic", "count"])
    expect(startVariablesOf(emptyBlueprint())).toEqual([])
  })

  it("maps node ids to node types", () => {
    expect(nodeTypeMapOf(blueprint)).toEqual({ start_1: "start", llm_1: "llm", end_1: "end" })
  })

  it("returns the label a user gave a node, if any", () => {
    expect(nodeLabelOf(blueprint, "llm_1")).toBe("Draft")
    expect(nodeLabelOf(blueprint, "start_1")).toBeUndefined()
    expect(nodeLabelOf(blueprint, "missing")).toBeUndefined()
  })

  it("gives each call its own empty blueprint", () => {
    expect(emptyBlueprint()).not.toBe(emptyBlueprint())
    expect(emptyBlueprint()).toEqual({ nodes: [], edges: [], viewport: { x: 0, y: 0, zoom: 1 } })
  })
})

describe("import and export", () => {
  it("names the export file after the workflow", () => {
    expect(exportFileName("My Workflow (v2)!")).toBe("my-workflow-v2.json")
    expect(exportFileName("")).toBe("workflow.json")
    expect(exportFileName(null)).toBe("workflow.json")
  })

  it("reads the blueprint from an export envelope or a bare file", () => {
    expect(blueprintOfImport({ workflow: { blueprint } })).toBe(blueprint)
    expect(blueprintOfImport({ blueprint })).toBe(blueprint)
    expect(blueprintOfImport({ something: "else" })).toBeUndefined()
    expect(blueprintOfImport(null)).toBeUndefined()
  })
})
