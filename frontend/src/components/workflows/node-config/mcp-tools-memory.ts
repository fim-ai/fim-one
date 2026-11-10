/**
 * The tool listings of MCP servers the step editor fetched a moment ago.
 *
 * Listing opens the server, and the form for a step is mounted again every
 * time the step is selected. A listing is kept here for `MCP_TOOLS_TTL_MS`
 * (the same time the API remembers it), so selecting the same step again
 * does not ask the server again. Only answers are kept, never failures,
 * and each is kept for the signed-in user it was given to.
 */
import { USER_KEY } from "@/lib/constants"
import type { McpStepTool } from "./mcp-arguments"

export const MCP_TOOLS_TTL_MS = 30_000

const remembered = new Map<string, { at: number; tools: McpStepTool[] }>()

/** Who is signed in, as far as the browser's storage says. */
function currentUser(): string {
  try {
    const stored = typeof window === "undefined" ? null : window.localStorage.getItem(USER_KEY)
    const id = stored ? (JSON.parse(stored) as { id?: unknown }).id : null
    return typeof id === "string" ? id : ""
  } catch {
    return ""
  }
}

const keyOf = (serverId: string) => `${currentUser()}\n${serverId}`

/** The listing kept for `serverId`, if it is still fresh. */
export function rememberedMcpTools(serverId: string, now = Date.now()): McpStepTool[] | null {
  const key = keyOf(serverId)
  const entry = remembered.get(key)
  if (!entry) return null
  if (now - entry.at >= MCP_TOOLS_TTL_MS) {
    remembered.delete(key)
    return null
  }
  return entry.tools
}

export function rememberMcpTools(serverId: string, tools: McpStepTool[], now = Date.now()): void {
  remembered.set(keyOf(serverId), { at: now, tools })
}

/** Forget one server's listing (before asking it again), or all of them. */
export function forgetMcpTools(serverId?: string): void {
  if (serverId === undefined) remembered.clear()
  else remembered.delete(keyOf(serverId))
}
