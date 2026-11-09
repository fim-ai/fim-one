import { describe, it, expect } from "vitest"
import { categories, itemByType, offeredCategories } from "@/components/workflows/node-palette"
import type { WorkflowNodeType } from "@/types/workflow"

/**
 * An instance can switch the Human Review (humanIntervention) node off. The
 * editor then stops offering it for adding, but a workflow saved earlier
 * still has to render the node so its author can remove it.
 */

const offeredTypes = (hidden: WorkflowNodeType[]) =>
  offeredCategories(new Set(hidden)).flatMap((cat) => cat.items.map((item) => item.type))

describe("offeredCategories", () => {
  it("offers every node type when nothing is hidden", () => {
    expect(offeredCategories(new Set())).toBe(categories)
    expect(offeredTypes([])).toContain("humanIntervention")
  })

  it("leaves a hidden node type out and keeps the rest", () => {
    const types = offeredTypes(["humanIntervention"])
    expect(types).not.toContain("humanIntervention")
    expect(types).toEqual(
      categories.flatMap((cat) => cat.items.map((item) => item.type)).filter(
        (type) => type !== "humanIntervention",
      ),
    )
  })

  it("drops a category whose every node type is hidden", () => {
    const keys = offeredCategories(new Set<WorkflowNodeType>(["conditionBranch"])).map(
      (cat) => cat.key,
    )
    expect(keys).not.toContain("categoryLogic")
  })

  it("keeps the icon lookup for hidden types, for nodes already on the canvas", () => {
    expect(itemByType.humanIntervention).toBeDefined()
  })
})
