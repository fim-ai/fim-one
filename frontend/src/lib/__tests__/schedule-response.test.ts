import { describe, expect, it } from "vitest"

import { scheduleOfResponse } from "@/lib/api"

// The schedule dialog read `cron`, `enabled`, `inputs`, `timezone` off a
// response whose fields are `schedule_*`: a saved schedule reopened blank.
describe("scheduleOfResponse", () => {
  it("reads the server's field names", () => {
    expect(
      scheduleOfResponse({
        schedule_cron: "0 9 * * *",
        schedule_enabled: true,
        schedule_inputs: { q: 1 },
        schedule_timezone: "Asia/Shanghai",
        next_run_at: "2026-01-01T01:00:00+00:00",
        held_by_run: { run_id: "r1", status: "waiting", started_at: null },
      }),
    ).toEqual({
      cron: "0 9 * * *",
      enabled: true,
      inputs: { q: 1 },
      timezone: "Asia/Shanghai",
      next_run_at: "2026-01-01T01:00:00+00:00",
      held_by_run: { run_id: "r1", status: "waiting", started_at: null },
    })
  })

  it("gives a workflow with no schedule empty values, not undefined", () => {
    expect(scheduleOfResponse({ schedule_cron: null, schedule_enabled: false })).toEqual({
      cron: "",
      enabled: false,
      inputs: null,
      timezone: "UTC",
      next_run_at: null,
      held_by_run: null,
    })
  })
})
