import { beforeEach, describe, expect, it, vi } from "vitest"

import { ApiError } from "@/lib/api"
import { decideApproval, settledStatusOf, settledStatusOfRequest } from "@/lib/workflow-approvals"

const confirmations = vi.hoisted(() => ({
  respondToConfirmation: vi.fn(),
  getConfirmationStatus: vi.fn(),
}))
vi.mock("@/lib/api/confirmations", () => confirmations)

beforeEach(() => vi.clearAllMocks())

describe("decideApproval", () => {
  it("sends the decision with a trimmed note, and no note when it is blank", async () => {
    confirmations.respondToConfirmation.mockResolvedValue({ status: "approved", decided_at: "x" })
    expect(await decideApproval("c1", "approve", "  ok  ")).toEqual({ outcome: "decided", status: "approved" })
    expect(confirmations.respondToConfirmation).toHaveBeenLastCalledWith("c1", "approve", "ok")
    expect(await decideApproval("c1", "reject", "   ")).toEqual({ outcome: "decided", status: "rejected" })
    expect(confirmations.respondToConfirmation).toHaveBeenLastCalledWith("c1", "reject", undefined)
  })

  it("cuts a note at the 2000 characters the server takes", async () => {
    confirmations.respondToConfirmation.mockResolvedValue({ status: "approved", decided_at: "x" })
    await decideApproval("c1", "approve", "x".repeat(2500))
    expect(confirmations.respondToConfirmation.mock.calls[0][2]).toHaveLength(2000)
  })

  it("answers a request that was already decided with how it ended", async () => {
    confirmations.respondToConfirmation.mockRejectedValue(
      new ApiError(409, "Already decided", "confirmation_already_decided", { status: "expired" }),
    )
    expect(await decideApproval("c1", "approve")).toEqual({ outcome: "already", status: "expired" })
  })

  it("throws any other refusal", async () => {
    const forbidden = new ApiError(403, "Forbidden", "confirmation_forbidden")
    confirmations.respondToConfirmation.mockRejectedValue(forbidden)
    await expect(decideApproval("c1", "approve")).rejects.toBe(forbidden)
  })
})

describe("settled status", () => {
  it("knows approved, rejected and expired; anything else is unknown", () => {
    expect(["approved", "rejected", "expired", "pending", undefined].map(settledStatusOf)).toEqual([
      "approved",
      "rejected",
      "expired",
      "unknown",
      "unknown",
    ])
  })

  it("reads how a request ended, and is unknown when it cannot be read", async () => {
    confirmations.getConfirmationStatus.mockResolvedValue({ status: "approved" })
    expect(await settledStatusOfRequest("c1")).toBe("approved")
    confirmations.getConfirmationStatus.mockRejectedValue(new ApiError(403, "Forbidden"))
    expect(await settledStatusOfRequest("c1")).toBe("unknown")
  })
})
