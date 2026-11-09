import { describe, it, expect, vi, beforeEach } from "vitest"

const apiFetch = vi.fn()
vi.mock("@/lib/api", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  ApiError: class ApiError extends Error {},
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }))

import { fetchModuleFlags } from "@/lib/module-flags"

describe("fetchModuleFlags", () => {
  beforeEach(() => {
    apiFetch.mockReset()
  })

  it("reads the Human Intervention switch from the version envelope", async () => {
    apiFetch.mockResolvedValue({
      modules: { skills: false, workflows: true },
      workflow_human_intervention: true,
    })
    expect(await fetchModuleFlags()).toEqual({
      skills: false,
      workflows: true,
      humanIntervention: true,
    })
    expect(apiFetch).toHaveBeenCalledWith("/api/version")
  })

  it("treats a server that does not report the switch as off", async () => {
    apiFetch.mockResolvedValue({ modules: { skills: true, workflows: true } })
    expect((await fetchModuleFlags()).humanIntervention).toBe(false)
  })

  it("fails closed when the request fails", async () => {
    apiFetch.mockRejectedValue(new Error("network"))
    expect(await fetchModuleFlags()).toEqual({
      skills: false,
      workflows: false,
      humanIntervention: false,
    })
  })
})
