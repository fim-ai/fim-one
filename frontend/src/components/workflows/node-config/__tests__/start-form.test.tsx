import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../../messages/en/workflows.json"
import { START_VARIABLE_TYPES, StartForm } from "@/components/workflows/node-config/start-form"
import type { StartNodeData } from "@/types/workflow"

afterEach(cleanup)

function renderForm(data: StartNodeData) {
  const setField = vi.fn()
  render(
    <NextIntlClientProvider locale="en" messages={{ workflows }} timeZone="UTC">
      <StartForm data={data} setField={setField} setFields={vi.fn()} otherNodes={[]} />
    </NextIntlClientProvider>,
  )
  return setField
}

describe("StartForm", () => {
  it("names the variable types in the user's language", () => {
    const labels = workflows as unknown as Record<string, string>
    expect(START_VARIABLE_TYPES.map((type) => labels[`configStartType_${type}`])).toEqual([
      "Text",
      "Number",
      "Boolean",
      "JSON",
      "List",
    ])
    renderForm({ variables: [{ name: "count", type: "number" }] })
    expect(screen.getByText("Number")).toBeTruthy()
    expect(screen.queryByText("number")).toBeNull()
  })

  it("has the input check off for a step saved without the flag, and switches it on", () => {
    const setField = renderForm({ variables: [] })
    const toggle = screen.getByRole("switch", { name: "Check inputs before a run starts" })
    expect(toggle.getAttribute("aria-checked")).toBe("false")
    fireEvent.click(toggle)
    expect(setField).toHaveBeenCalledWith("validate_inputs", true)
  })

  it("shows the input check on when the step has it, and switches it off", () => {
    const setField = renderForm({ variables: [], validate_inputs: true })
    const toggle = screen.getByRole("switch", { name: "Check inputs before a run starts" })
    expect(toggle.getAttribute("aria-checked")).toBe("true")
    fireEvent.click(toggle)
    expect(setField).toHaveBeenCalledWith("validate_inputs", false)
  })
})
