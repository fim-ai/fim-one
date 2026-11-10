import { describe, expect, it } from "vitest"

import { MCP_SERVER_ADDRESS_PRIVATE, mcpServerNotice } from "../mcp-server-notice"

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
