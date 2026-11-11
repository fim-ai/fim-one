import { describe, expect, it } from "vitest"

import {
  auditActionKey,
  buildAuditQuery,
  callDurationMs,
  callRequestLine,
  callStatus,
  describeUserAgent,
  formatAuditChange,
  normalizeAuditPage,
  normalizeAuditRecord,
} from "@/lib/audit-records"

describe("normalizeAuditRecord", () => {
  it("reads the platform list's row, whose actor is admin_id / admin_username", () => {
    const record = normalizeAuditRecord({
      id: "1",
      admin_id: "u-1",
      admin_username: "root",
      action: "org.update",
      org_id: "org-a",
      outcome: "denied",
      ip_address: "198.51.100.9",
      user_agent: "UA/1",
      created_at: "2026-11-11T08:00:00",
      changes: { role: { from: "member", to: "admin" } },
    })
    expect(record.actor_id).toBe("u-1")
    expect(record.actor_label).toBe("root")
    expect(record.source).toBe("audit")
    expect(record.outcome).toBe("denied")
    expect(record.ip_address).toBe("198.51.100.9")
    expect(record.changes).toEqual({ role: { from: "member", to: "admin" } })
  })

  it("reads an organization's row, which has no address fields", () => {
    const record = normalizeAuditRecord({
      id: "2",
      source: "call",
      actor_id: "u-2",
      actor_label: "mia",
      action: "call.connector",
      outcome: "failed",
      created_at: "2026-11-11T08:00:00",
      extra: { request_method: "GET", request_url: "https://crm.example/customers", response_status: 500, response_time_ms: 41 },
    })
    expect(record.actor_label).toBe("mia")
    expect(record.ip_address).toBeNull()
    expect(record.user_agent).toBeNull()
    expect(callRequestLine(record)).toBe("GET https://crm.example/customers")
    expect(callStatus(record)).toBe(500)
    expect(callDurationMs(record)).toBe(41)
  })

  it("treats a row from before the newer columns as a successful action", () => {
    const record = normalizeAuditRecord({ id: "3", admin_id: "", admin_username: "root", action: "settings.update" })
    expect(record.outcome).toBe("success")
    expect(record.actor_id).toBeNull()
    expect(record.org_id).toBeNull()
    expect(record.changes).toBeNull()
    expect(record.extra).toEqual({})
    expect(callRequestLine(record)).toBeNull()
  })

  it("normalizes a page and survives one with nothing in it", () => {
    expect(normalizeAuditPage({})).toEqual({ items: [], total: 0, page: 1, size: 50, pages: 1 })
    const page = normalizeAuditPage({ items: [{ id: "1", action: "x" }], total: 1, page: 2, size: 20, pages: 3 })
    expect(page.items[0].id).toBe("1")
    expect([page.total, page.page, page.size, page.pages]).toEqual([1, 2, 20, 3])
  })
})

describe("buildAuditQuery", () => {
  it("leaves out a filter that filters nothing", () => {
    const params = buildAuditQuery({ page: 1, size: 50, action: "__all__", orgId: "__all__", outcome: "", ipAddress: "  " })
    expect(params.toString()).toBe("page=1&size=50")
  })

  it("sends the organization, outcome, address and day filters", () => {
    const params = buildAuditQuery({
      page: 2,
      size: 50,
      action: "org.update",
      orgId: "org-a",
      outcome: "denied",
      ipAddress: " 198.51.100.9 ",
      dateFrom: "2026-11-01",
      dateTo: "2026-11-11",
    })
    expect(Object.fromEntries(params)).toEqual({
      page: "2",
      size: "50",
      action: "org.update",
      org_id: "org-a",
      outcome: "denied",
      ip_address: "198.51.100.9",
      date_from: "2026-11-01",
      date_to: "2026-11-11",
    })
  })

  it("names the source only when it is not the event table, and the actor as the endpoint does", () => {
    expect(buildAuditQuery({ source: "audit" }).toString()).toBe("")
    expect(buildAuditQuery({ source: "call" }).toString()).toBe("source=call")
    expect(buildAuditQuery({ actorId: "u-1" }).toString()).toBe("admin_id=u-1")
    expect(buildAuditQuery({ actorId: "u-1", actorParam: "actor_id" }).toString()).toBe("actor_id=u-1")
  })
})

describe("formatting", () => {
  it("shows a change as before and after, and a secret as changed", () => {
    expect(formatAuditChange({ from: "member", to: "admin" }, "changed")).toBe("member → admin")
    expect(formatAuditChange({ from: null, to: true }, "changed")).toBe("— → true")
    expect(formatAuditChange({ changed: true }, "changed")).toBe("changed")
  })

  it("turns an action name into its message key", () => {
    expect(auditActionKey("org.member_role_change")).toBe("org__member_role_change")
    expect(auditActionKey("call.mcp")).toBe("call__mcp")
  })

  it("describes a browser, telling Edge from Chrome and Chrome from Safari", () => {
    const mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
    expect(describeUserAgent(`${mac} Chrome/130.0 Safari/537.36`)).toBe("Chrome on macOS")
    expect(describeUserAgent(`${mac} Chrome/130.0 Safari/537.36 Edg/130.0`)).toBe("Edge on macOS")
    expect(describeUserAgent(`${mac} Version/17.0 Safari/605.1.15`)).toBe("Safari on macOS")
    expect(describeUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Safari/604.1")).toBe("Safari on iOS")
    expect(describeUserAgent("curl/8.4.0")).toBe("curl/8.4.0")
    expect(describeUserAgent(null)).toBeNull()
  })
})
