import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { formatDate, formatDateTime, formatRelativeTime, parseApiDate } from "@/lib/date-utils"

// The machine these run on may be in any zone; the bug only shows away from
// UTC, so the zone is pinned to UTC+8 for the file.
const ORIGINAL_TZ = process.env.TZ
beforeEach(() => {
  process.env.TZ = "Asia/Shanghai"
  vi.useFakeTimers()
  vi.setSystemTime(new Date("2026-03-23T08:00:10Z"))
})
afterEach(() => {
  vi.useRealTimers()
  process.env.TZ = ORIGINAL_TZ
})

describe("parseApiDate", () => {
  it("reads a date-time without a zone as UTC", () => {
    expect(new Date("2026-03-23T08:00:00").getTimezoneOffset()).toBe(-480)
    expect(parseApiDate("2026-03-23T08:00:00").toISOString()).toBe("2026-03-23T08:00:00.000Z")
    expect(parseApiDate("2026-03-23T08:00:00.123456").toISOString()).toBe("2026-03-23T08:00:00.123Z")
    expect(parseApiDate("2026-03-23 08:00:00").toISOString()).toBe("2026-03-23T08:00:00.000Z")
    expect(parseApiDate("2026-03-23T08:00").toISOString()).toBe("2026-03-23T08:00:00.000Z")
  })

  it("leaves a string that names its zone as it is", () => {
    expect(parseApiDate("2026-03-23T08:00:00Z").toISOString()).toBe("2026-03-23T08:00:00.000Z")
    expect(parseApiDate("2026-03-23T08:00:00+00:00").toISOString()).toBe("2026-03-23T08:00:00.000Z")
    expect(parseApiDate("2026-03-23T16:00:00+08:00").toISOString()).toBe("2026-03-23T08:00:00.000Z")
    expect(parseApiDate("2026-03-23T03:00:00.5-05:00").toISOString()).toBe("2026-03-23T08:00:00.500Z")
  })

  it("leaves a date without a time as it is", () => {
    expect(parseApiDate("2026-03-23").toISOString()).toBe("2026-03-23T00:00:00.000Z")
  })
})

describe("formatting a timestamp the API sent without a zone", () => {
  // A version or a run made 10 seconds ago, as the SQLite backend reports it.
  const justNow = "2026-03-23T08:00:00.000000"

  it("something made just now is not 'about 8 hours ago'", () => {
    expect(formatRelativeTime(justNow, { locale: "en" })).toBe("less than a minute ago")
    expect(formatRelativeTime("2026-03-23T07:00:10", { locale: "en" })).toBe("about 1 hour ago")
  })

  it("says the same as for the zoned form of that instant", () => {
    const zoned = "2026-03-23T08:00:00+00:00"
    expect(formatRelativeTime(justNow, { locale: "en" })).toBe(formatRelativeTime(zoned, { locale: "en" }))
    const opts = { locale: "en-US", timezone: "Asia/Shanghai" }
    expect(formatDateTime(justNow, opts)).toBe(formatDateTime(zoned, opts))
    expect(formatDateTime(justNow, opts)).toContain("04:00")
    expect(formatDate("2026-03-23T23:30:00", opts)).toBe("Mar 24, 2026")
  })

  it("keeps the fallback for a missing value", () => {
    expect(formatRelativeTime(null, { fallback: "never" })).toBe("never")
  })
})
