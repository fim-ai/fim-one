import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../messages/en/workflows.json"
import errors from "../../../../messages/en/errors.json"
import { RunPanel } from "@/components/workflows/run-panel"

afterEach(cleanup)

const VARIABLES = [
  { name: "count", type: "number", required: true },
  { name: "topic", type: "string" },
  { name: "options", type: "object" },
]

function renderPanel(props: Partial<Parameters<typeof RunPanel>[0]> = {}) {
  const onStartRun = vi.fn()
  const view = render(
    <NextIntlClientProvider locale="en" messages={{ workflows, errors }} timeZone="UTC">
      <RunPanel
        isOpen
        isRunning={false}
        startVariables={VARIABLES}
        nodeResults={null}
        finalOutputs={null}
        finalError={null}
        runDuration={null}
        nodeTypeMap={{}}
        totalNodeCount={0}
        logEvents={[]}
        onStartRun={onStartRun}
        onRunAgain={() => {}}
        onCancel={() => {}}
        onClose={() => {}}
        {...props}
      />
    </NextIntlClientProvider>,
  )
  return { onStartRun, ...view }
}

describe("RunPanel input form", () => {
  it("sends what was typed as text and leaves empty fields out when the Start step checks inputs", () => {
    const { onStartRun } = renderPanel({ checkInputs: true })
    fireEvent.change(screen.getByLabelText(/^count/), { target: { value: "abc" } })
    fireEvent.change(screen.getByLabelText(/^options/), { target: { value: '{"a": 1}' } })
    expect(screen.getByLabelText(/^options/).tagName).toBe("TEXTAREA")
    fireEvent.click(screen.getByRole("button", { name: "Start Run" }))
    expect(onStartRun).toHaveBeenCalledWith({ count: "abc", options: '{"a": 1}' })
  })

  it("converts in the form, as before, when the Start step does not check inputs", () => {
    const { onStartRun } = renderPanel()
    fireEvent.change(screen.getByLabelText(/^count/), { target: { value: "12" } })
    expect(screen.getByLabelText(/^options/).tagName).toBe("INPUT")
    fireEvent.click(screen.getByRole("button", { name: "Start Run" }))
    expect(onStartRun).toHaveBeenCalledWith({ count: 12, topic: "", options: "" })
  })

  it("shows the server's refusal on the input it names and keeps what was typed", () => {
    const onClearInputError = vi.fn()
    renderPanel({
      checkInputs: true,
      inputError: { input: "count", message: 'Input "count" must be a number.' },
      onClearInputError,
    })
    const field = screen.getByLabelText(/^count/)
    expect(field.getAttribute("aria-invalid")).toBe("true")
    expect(screen.getByLabelText(/^topic/).getAttribute("aria-invalid")).toBe("false")
    const message = screen.getByText('Input "count" must be a number.')
    expect(message.className).toContain("text-destructive")
    expect(screen.getByTestId("run-input-count").contains(message)).toBe(true)
    // Still the form, not a failed run.
    expect(screen.getByRole("button", { name: "Start Run" })).toBeTruthy()
    expect(screen.queryByTestId("run-status")).toBeNull()

    fireEvent.change(field, { target: { value: "12" } })
    expect(onClearInputError).toHaveBeenCalled()
  })

  it("keeps the typed values when a refusal arrives", () => {
    const { rerender } = renderPanel({ checkInputs: true })
    fireEvent.change(screen.getByLabelText(/^count/), { target: { value: "abc" } })
    rerender(
      <NextIntlClientProvider locale="en" messages={{ workflows, errors }} timeZone="UTC">
        <RunPanel
          isOpen
          isRunning={false}
          checkInputs
          inputError={{ input: "count", message: 'Input "count" must be a number.' }}
          startVariables={VARIABLES}
          nodeResults={null}
          finalOutputs={null}
          finalError={null}
          runDuration={null}
          nodeTypeMap={{}}
          totalNodeCount={0}
          logEvents={[]}
          onStartRun={() => {}}
          onRunAgain={() => {}}
          onCancel={() => {}}
          onClose={() => {}}
        />
      </NextIntlClientProvider>,
    )
    expect((screen.getByLabelText(/^count/) as HTMLInputElement).value).toBe("abc")
  })
})
