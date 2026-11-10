import type { MCPServerResponse } from "@/types/mcp-server"

/** The backend's code for "only the owner can connect to this server". */
export const MCP_SERVER_ADDRESS_PRIVATE = "mcp_server_address_private"

/**
 * What an MCP server's card has to tell whoever looks at it, if anything.
 *
 * - `addressPrivateOwner`: the owner of a shared server whose address or
 *   launch arguments carry a credential while fallback is off. Nobody else
 *   can use it.
 * - `addressPrivateMember`: someone else looking at such a server. Keys of
 *   their own would not help, so they are not asked for any.
 * - `keysRequired`: someone else, fallback off, no keys of their own saved.
 */
export type MCPServerNotice = "addressPrivateOwner" | "addressPrivateMember" | "keysRequired"

/** The parts of an address the backend can name; each has a label in `tools.addressReasons`. */
export const ADDRESS_REASONS = [
  "url_user_info",
  "url_query",
  "url_fragment",
  "url_path_segment",
  "url_path_parameter",
  "url_token",
  "url_unparseable",
  "arg_name",
  "arg_token",
] as const
export type AddressReason = (typeof ADDRESS_REASONS)[number]

/** The reasons of a response that this interface has a label for, in the order given. */
export function knownAddressReasons(reasons: readonly string[] | null | undefined): AddressReason[] {
  return (reasons ?? []).filter((r): r is AddressReason =>
    (ADDRESS_REASONS as readonly string[]).includes(r),
  )
}

type NoticeFields = Pick<
  MCPServerResponse,
  "allow_fallback" | "address_private" | "warning" | "my_has_credentials"
>

export function mcpServerNotice(server: NoticeFields, isOwner: boolean): MCPServerNotice | null {
  if (isOwner) {
    return server.warning === MCP_SERVER_ADDRESS_PRIVATE ? "addressPrivateOwner" : null
  }
  if (server.address_private) return "addressPrivateMember"
  if (!server.allow_fallback && !server.my_has_credentials) return "keysRequired"
  return null
}
