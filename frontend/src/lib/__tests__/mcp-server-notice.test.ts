import { describe, expect, it } from "vitest"

import toolsMessages from "../../../messages/en/tools.json"
import {
  ADDRESS_REASONS,
  MCP_SERVER_ADDRESS_PRIVATE,
  canConfigureOwnKeys,
  depsNeedingKeys,
  depsOnlyOwnerCanUse,
  knownAddressReasons,
  mcpServerNotice,
} from "../mcp-server-notice"

const base = { allow_fallback: false, address_private: false, warning: null, my_has_credentials: false }

describe("mcpServerNotice", () => {
  it("asks someone else for keys when fallback is off and they saved none", () => {
    expect(mcpServerNotice(base, false)).toBe("keysRequired")
    expect(mcpServerNotice({ ...base, my_has_credentials: true }, false)).toBeNull()
    expect(mcpServerNotice({ ...base, allow_fallback: true }, false)).toBeNull()
  })

  it("does not ask for keys that would not help", () => {
    const server = { ...base, address_private: true }
    expect(mcpServerNotice(server, false)).toBe("addressPrivateMember")
    // Not even once they have saved some.
    expect(mcpServerNotice({ ...server, my_has_credentials: true }, false)).toBe(
      "addressPrivateMember",
    )
  })

  it("warns the owner only when the backend says the server is shared", () => {
    const flagged = { ...base, address_private: true }
    expect(mcpServerNotice(flagged, true)).toBeNull()
    expect(mcpServerNotice({ ...flagged, warning: MCP_SERVER_ADDRESS_PRIVATE }, true)).toBe(
      "addressPrivateOwner",
    )
  })

  it("never asks the owner for keys", () => {
    expect(mcpServerNotice(base, true)).toBeNull()
  })

  it("reads a response from before the fields existed as unmarked", () => {
    expect(mcpServerNotice({ allow_fallback: true, my_has_credentials: false }, false)).toBeNull()
  })
})

describe("knownAddressReasons", () => {
  it("keeps the reasons this interface can word, in the order given", () => {
    expect(knownAddressReasons(["url_query", "something_new", "arg_name"])).toEqual([
      "url_query",
      "arg_name",
    ])
    expect(knownAddressReasons(undefined)).toEqual([])
  })

  it("has a label for every reason", () => {
    const labels = (toolsMessages as { addressReasons: Record<string, string> }).addressReasons
    expect(Object.keys(labels).sort()).toEqual([...ADDRESS_REASONS].sort())
    // The warning names the part and says what switching fallback on lends.
    expect(toolsMessages.addressPrivateWarning).toContain("{parts}")
    expect(toolsMessages.addressPrivateWarning).toContain("may contain a credential")
    expect(toolsMessages.addressPrivateWarning).toContain("headers and environment variables")
  })
})

describe("keys that would not help are not asked for", () => {
  it("offers Configure My Keys only where a key of one's own opens the server", () => {
    expect(canConfigureOwnKeys(base, false)).toBe(true)
    expect(canConfigureOwnKeys({ ...base, address_private: true }, false)).toBe(false)
    expect(canConfigureOwnKeys(base, true)).toBe(false)
  })

  it("splits a subscription's dependencies into those needing keys and those owner-only", () => {
    type Dep = { resource_id: string; allow_fallback: boolean; address_private?: boolean }
    const lent: Dep = { resource_id: "a", allow_fallback: true }
    const needsKeys: Dep = { resource_id: "b", allow_fallback: false }
    const ownerOnly: Dep = { resource_id: "c", allow_fallback: false, address_private: true }
    const deps = [lent, needsKeys, ownerOnly]
    expect(depsNeedingKeys(deps)).toEqual([needsKeys])
    expect(depsOnlyOwnerCanUse(deps)).toEqual([ownerOnly])
    expect(depsNeedingKeys(undefined)).toEqual([])
    expect(depsOnlyOwnerCanUse(null)).toEqual([])
  })
})
