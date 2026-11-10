import { describe, expect, it } from "vitest"

import {
  DATABASE_OPERATIONS,
  actionOptions,
  connectorOptions,
  databaseParameters,
  isDatabaseStep,
  listAllConnectors,
} from "@/components/workflows/node-config/connector-options"
import type { ConnectorActionResponse, ConnectorResponse } from "@/types/connector"

/**
 * The Connector step used to render both dropdowns with nothing but their
 * placeholder, so a connector could not be chosen in the editor at all.
 * These are the lists the dropdowns now show.
 */

function action(id: string, connectorId: string, name: string): ConnectorActionResponse {
  return {
    id,
    connector_id: connectorId,
    name,
    description: null,
    method: "GET",
    path: "/",
    parameters_schema: null,
    request_body_template: null,
    response_extract: null,
    requires_confirmation: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
  }
}

function connector(
  id: string,
  name: string,
  overrides: Partial<ConnectorResponse> = {},
): ConnectorResponse {
  return {
    id,
    name,
    description: null,
    icon: null,
    type: "api",
    base_url: "https://example.com",
    auth_type: "none",
    auth_config: null,
    is_official: false,
    is_active: true,
    forked_from: null,
    version: 1,
    visibility: "personal",
    org_id: null,
    user_id: "u1",
    has_default_credentials: false,
    allow_fallback: false,
    actions: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
    ...overrides,
  }
}

const crm = connector("c-crm", "CRM", {
  actions: [action("a-list", "c-crm", "list_contacts"), action("a-get", "c-crm", "get_contact")],
})
const mail = connector("c-mail", "Mail", { actions: [action("a-send", "c-mail", "send")] })
const warehouse = connector("c-db", "Warehouse", { type: "database" })
const retired = connector("c-old", "Retired", { is_active: false })

describe("connectorOptions", () => {
  it("lists the user's API connectors by name", () => {
    expect(connectorOptions([crm, mail], "", "u1")).toEqual([
      { value: "c-crm", label: "CRM" },
      { value: "c-mail", label: "Mail" },
    ])
  })

  it("leaves out disabled connectors", () => {
    expect(connectorOptions([crm, retired], "", "u1").map((o) => o.value)).toEqual(["c-crm"])
  })

  // A Connector step could not run a database connector at all, so the
  // list left every one of them out. It can now, for the connector's owner.
  it("lists a database connector for its owner", () => {
    expect(connectorOptions([crm, warehouse], "", "u1")).toEqual([
      { value: "c-crm", label: "CRM" },
      { value: "c-db", label: "Warehouse" },
    ])
  })

  it("leaves out a database connector that belongs to someone else", () => {
    const shared = connector("c-shared-db", "Shared warehouse", { type: "database", user_id: "u2" })
    expect(connectorOptions([crm, shared], "", "u1").map((o) => o.value)).toEqual(["c-crm"])
    expect(connectorOptions([crm, warehouse], "", null).map((o) => o.value)).toEqual(["c-crm"])
  })

  it("keeps a disabled connector the step already uses, by name", () => {
    expect(connectorOptions([crm, retired], "c-old", "u1")).toContainEqual({
      value: "c-old",
      label: "Retired",
    })
  })

  it("keeps a connector that is not in the list, by id", () => {
    expect(connectorOptions([], "c-gone", "u1")).toEqual([{ value: "c-gone", label: "c-gone" }])
    expect(connectorOptions([crm], "c-gone", "u1")).toContainEqual({
      value: "c-gone",
      label: "c-gone",
    })
  })
})

describe("database steps", () => {
  it("knows a database step from its connector", () => {
    expect(isDatabaseStep([crm, warehouse], "c-db", "")).toBe(true)
    expect(isDatabaseStep([crm, warehouse], "c-crm", "a-list")).toBe(false)
    // An API connector whose action happens to be named like an operation.
    expect(isDatabaseStep([crm], "c-crm", "query")).toBe(false)
  })

  it("goes by the stored operation while the connector is not loaded", () => {
    expect(isDatabaseStep([], "c-db", "query")).toBe(true)
    expect(isDatabaseStep([], "c-db", "a-list")).toBe(false)
    expect(isDatabaseStep([], "", "")).toBe(false)
  })

  it("gives each operation its own parameters and keeps what is already there", () => {
    expect(databaseParameters("list_tables", { sql: "SELECT 1" })).toEqual({})
    expect(databaseParameters("describe_table", { sql: "SELECT 1" })).toEqual({ table_name: "" })
    expect(databaseParameters("describe_table", { table_name: "orders" })).toEqual({
      table_name: "orders",
    })
    expect(databaseParameters("query", { sql: "SELECT 1", q: "left over" })).toEqual({
      sql: "SELECT 1",
    })
    expect(databaseParameters("", { sql: "SELECT 1" })).toEqual({})
  })

  it("names the three operations chat has", () => {
    expect([...DATABASE_OPERATIONS]).toEqual(["list_tables", "describe_table", "query"])
  })
})

describe("actionOptions", () => {
  it("lists the actions of the chosen connector only", () => {
    expect(actionOptions([crm, mail], "c-crm", "")).toEqual([
      { value: "a-list", label: "list_contacts" },
      { value: "a-get", label: "get_contact" },
    ])
    expect(actionOptions([crm, mail], "c-mail", "")).toEqual([{ value: "a-send", label: "send" }])
  })

  it("is empty until a connector is chosen", () => {
    expect(actionOptions([crm, mail], "", "")).toEqual([])
  })

  it("keeps an action that is not in the list, by id", () => {
    expect(actionOptions([crm], "c-crm", "a-removed")).toContainEqual({
      value: "a-removed",
      label: "a-removed",
    })
    expect(actionOptions([], "c-crm", "a-list")).toEqual([{ value: "a-list", label: "a-list" }])
  })
})

describe("listAllConnectors", () => {
  // The listing serves 100 per request. Only the first page used to be
  // loaded, so a user's 101st connector could not be chosen.
  it("loads every page", async () => {
    const pages = [
      [connector("c-1", "One"), connector("c-2", "Two")],
      [connector("c-3", "Three")],
      [connector("c-4", "Four")],
    ]
    const calls: Array<[number, number]> = []
    const all = await listAllConnectors(async (page, size) => {
      calls.push([page, size])
      return { items: pages[page - 1], pages: pages.length }
    })
    expect(all.map((c) => c.id)).toEqual(["c-1", "c-2", "c-3", "c-4"])
    expect(calls).toEqual([
      [1, 100],
      [2, 100],
      [3, 100],
    ])
  })

  it("asks once when there is one page, or none", async () => {
    let calls = 0
    const one = await listAllConnectors(async () => {
      calls += 1
      return { items: [crm], pages: 1 }
    })
    expect(one).toEqual([crm])
    const none = await listAllConnectors(async () => {
      calls += 1
      return { items: [], pages: 0 }
    })
    expect(none).toEqual([])
    expect(calls).toBe(2)
  })
})
