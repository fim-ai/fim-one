import { useState } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import workflows from "../../../../../messages/en/workflows.json"
import common from "../../../../../messages/en/common.json"
import { DecisionControls } from "@/components/workflows/approvals/decision-controls"
import {
  hiddenArgumentCharacters,
  RequestDetails,
} from "@/components/workflows/approvals/request-details"

afterEach(cleanup)

const long = { kind: "confirmation", arguments: { body: "x".repeat(600) } } as const
const short = { kind: "confirmation", arguments: { target: "ledger-7" } } as const

// Long arguments start collapsed, and the request could be approved
// without ever opening them.
describe("a request whose arguments are collapsed", () => {
  it("counts what is hidden, and nothing for short arguments or an approval step", () => {
    expect(hiddenArgumentCharacters(long)).toBeGreaterThan(600)
    expect(hiddenArgumentCharacters(short)).toBe(0)
    expect(hiddenArgumentCharacters({ kind: "workflow_approval", arguments: null })).toBe(0)
  })

  it("cannot be approved until they have been opened; rejecting is always possible", () => {
    function Card() {
      const [seen, setSeen] = useState(false)
      const hidden = hiddenArgumentCharacters(long)
      return (
        <>
          <RequestDetails
            item={{ ...long, title: "Confirm", description: null, action: "Ledger.wipe" }}
            onExpanded={() => setSeen(true)}
          />
          <DecisionControls
            busy={false}
            onDecide={onDecide}
            approveBlockedReason={hidden > 0 && !seen ? `Open the arguments (${hidden})` : undefined}
          />
        </>
      )
    }
    const onDecide = vi.fn()
    render(
      <NextIntlClientProvider locale="en" messages={{ workflows, common }} timeZone="UTC">
        <Card />
      </NextIntlClientProvider>,
    )
    const approve = screen.getByRole("button", { name: workflows.approvalApprove }) as HTMLButtonElement
    const reject = screen.getByRole("button", { name: workflows.approvalReject }) as HTMLButtonElement
    expect(approve.disabled).toBe(true)
    expect(reject.disabled).toBe(false)
    expect(screen.getByRole("note").textContent).toContain("Open the arguments")

    fireEvent.click(screen.getByRole("button", { name: workflows.approvalArguments }))

    expect(approve.disabled).toBe(false)
    expect(screen.queryByRole("note")).toBeNull()
    fireEvent.click(approve)
    expect(onDecide).toHaveBeenCalledWith("approve", "")
  })
})
