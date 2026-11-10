import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, render, screen } from "@testing-library/react"
import { NextIntlClientProvider } from "next-intl"

import playground from "../../../../messages/en/playground.json"
import common from "../../../../messages/en/common.json"
import errors from "../../../../messages/en/errors.json"
import { DagOutput } from "@/components/playground/dag-output"
import type { AwaitingConfirmationEvent, DagDoneEvent } from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"

/**
 * A tool call that needs approval inside a DAG step raises the same
 * `awaiting_confirmation` event as in a ReAct turn. The DAG view used to
 * drop it, so the step waited two minutes for a card nobody could see.
 */

// The card itself is covered where ReAct uses it; here only that the DAG
// view hands each request to it.
vi.mock("@/components/chat/confirmation-card", () => ({
  ConfirmationCard: (props: { confirmationId: string; toolName: string; mode?: string }) => (
    <div data-testid="confirmation-card" data-id={props.confirmationId} data-mode={props.mode}>
      {props.toolName}
    </div>
  ),
}))
vi.mock("@/contexts/auth-context", () => ({ useAuth: () => ({ user: null }) }))
vi.mock("@/components/dag/dag-flow-graph", () => ({ DagFlowGraph: () => null }))
vi.mock("@/hooks/use-tool-catalog", () => ({
  useToolCatalog: () => ({ data: null, tools: [], categories: [], isLoading: false }),
}))

function confirmation(id: string): AwaitingConfirmationEvent {
  return {
    type: "awaiting_confirmation",
    confirmation_id: id,
    tool_name: "oa__pay",
    arguments: { amount: 5 },
    timeout_at: "2030-01-01T00:00:00+00:00",
    agent_id: "",
    mode: "inline",
    channel_label: "",
    approver_scope: "initiator",
  }
}

const runningStep: StepState = {
  step_id: "s1",
  task: "Pay the invoice",
  status: "running",
  tools_used: [],
  iterations: [],
}

function renderDag(props: Partial<Parameters<typeof DagOutput>[0]>) {
  return render(
    <NextIntlClientProvider locale="en" messages={{ playground, common, errors }}>
      <DagOutput
        planSteps={undefined}
        stepStates={[]}
        analysisPhase={null}
        doneEvent={null}
        currentPhase="executing"
        hideDagGraph
        {...props}
      />
    </NextIntlClientProvider>,
  )
}

afterEach(cleanup)

describe("DagOutput approval requests", () => {
  it("shows a card for each request while the turn is running", () => {
    renderDag({
      stepStates: [runningStep],
      confirmationEvents: [confirmation("c-1"), confirmation("c-2")],
    })

    const cards = screen.getAllByTestId("confirmation-card")
    expect(cards.map((c) => c.getAttribute("data-id"))).toEqual(["c-1", "c-2"])
    expect(cards[0].getAttribute("data-mode")).toBe("inline")
  })

  it("keeps the card after the turn is done", () => {
    const done = { answer: "Paid.", achieved: true, elapsed: 3 } as DagDoneEvent
    renderDag({
      stepStates: [{ ...runningStep, status: "completed" }],
      doneEvent: done,
      currentPhase: null,
      confirmationEvents: [confirmation("c-1")],
    })

    expect(screen.getAllByTestId("confirmation-card")).toHaveLength(1)
  })

  it("shows none when the turn asked for nothing", () => {
    renderDag({ stepStates: [runningStep] })
    expect(screen.queryByTestId("confirmation-card")).toBeNull()
  })
})
