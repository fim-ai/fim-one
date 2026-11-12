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

/** Why a stdio server cannot be configured by this user, as `GET /api/mcp-servers/capabilities` says it. */
export type StdioUnavailableReason = "disabled" | "admin_only"

/**
 * The `tools` message that tells a user why the form offers no stdio
 * transport, or `null` when it does. A stdio server runs a command inside
 * the application, so only a system administrator may configure one; until
 * the capabilities are known the form offers none.
 */
export function stdioUnavailableMessageKey(
  allowStdio: boolean,
  reason: StdioUnavailableReason | null | undefined,
): "stdioUnavailableAdminOnly" | "stdioUnavailableDisabled" | null {
  if (allowStdio) return null
  return reason === "admin_only" ? "stdioUnavailableAdminOnly" : "stdioUnavailableDisabled"
}

/** The `tools` message for a stdio server that is never started (`stdio_blocked`), or `null`. */
export function stdioBlockedMessageKey(
  server: Pick<MCPServerResponse, "stdio_blocked">,
): "stdioBlockedOwnerNotAdmin" | "stdioBlockedDisabled" | null {
  if (!server.stdio_blocked) return null
  return server.stdio_blocked === "stdio_mcp_disabled"
    ? "stdioBlockedDisabled"
    : "stdioBlockedOwnerNotAdmin"
}

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
  "arg_env",
  "arg_connection_string",
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

/** Whether "Configure My Keys" is of any use to someone who is not the owner. */
export function canConfigureOwnKeys(server: NoticeFields, isOwner: boolean): boolean {
  return !isOwner && !server.address_private
}

/** The dependencies of a just-subscribed resource that the user is asked to set keys for. */
export function depsNeedingKeys<T extends { allow_fallback: boolean; address_private?: boolean }>(
  deps: readonly T[] | null | undefined,
): T[] {
  return (deps ?? []).filter((dep) => !dep.allow_fallback && !dep.address_private)
}

/** Those that only their owner can use: keys of the subscriber's own would not help. */
export function depsOnlyOwnerCanUse<T extends { address_private?: boolean }>(
  deps: readonly T[] | null | undefined,
): T[] {
  return (deps ?? []).filter((dep) => !!dep.address_private)
}

export function mcpServerNotice(server: NoticeFields, isOwner: boolean): MCPServerNotice | null {
  if (isOwner) {
    return server.warning === MCP_SERVER_ADDRESS_PRIVATE ? "addressPrivateOwner" : null
  }
  if (server.address_private) return "addressPrivateMember"
  if (!server.allow_fallback && !server.my_has_credentials) return "keysRequired"
  return null
}
