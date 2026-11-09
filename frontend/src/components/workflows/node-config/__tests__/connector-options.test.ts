import { describe, expect, it } from "vitest"

import {
  actionOptions,
  connectorOptions,
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
    expect(connectorOptions([crm, mail], "")).toEqual([
      { value: "c-crm", label: "CRM" },
      { value: "c-mail", label: "Mail" },
    ])
  })

  it("leaves out database connectors and disabled ones", () => {
    expect(connectorOptions([crm, warehouse, retired], "").map((o) => o.value)).toEqual(["c-crm"])
  })

  it("keeps a disabled connector the step already uses, by name", () => {
    expect(connectorOptions([crm, retired], "c-old")).toContainEqual({
      value: "c-old",
      label: "Retired",
    })
  })

  it("keeps a connector that is not in the list, by id", () => {
    expect(connectorOptions([], "c-gone")).toEqual([{ value: "c-gone", label: "c-gone" }])
    expect(connectorOptions([crm], "c-gone")).toContainEqual({ value: "c-gone", label: "c-gone" })
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
