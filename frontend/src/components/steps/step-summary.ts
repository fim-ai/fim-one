/** Tool display name mapping: internal_name → friendly name */
const TOOL_DISPLAY_NAMES: Record<string, string> = {
  web_fetch: "Fetch Webpage",
  web_search: "Web Search",
  python_exec: "Python",
  calculator: "Calculator",
  file_ops: "File",
  kb_retrieve: "Knowledge Base",
  grounded_retrieve: "Knowledge Base",
  shell_exec: "Shell",
  http_request: "HTTP Request",
}

/** Get a human-friendly display name for a tool. */
export function getToolDisplayName(tool_name: string): string {
  if (TOOL_DISPLAY_NAMES[tool_name]) return TOOL_DISPLAY_NAMES[tool_name]

  // Connector: connector__action → "Connector"
  if (tool_name.includes("__") && !tool_name.startsWith("mcp__")) {
    return tool_name.split("__")[0].replace(/^\w/, (c) => c.toUpperCase())
  }

  // MCP: mcp__service__action → "Service"
  if (tool_name.startsWith("mcp__")) {
    const parts = tool_name.split("__")
    if (parts.length >= 3) {
      return parts[1].replace(/^\w/, (c) => c.toUpperCase())
    }
  }

  // Fallback: snake_case → Title Case
  return tool_name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * Generate a short English summary for a tool call step.
 * Pure template-based — no LLM needed.
 */
export function generateStepSummary(
  tool_name?: string,
  args?: Record<string, unknown>,
  reasoning?: string,
): string {
  // 1. Short reasoning → use directly
  if (reasoning && reasoning.length < 60) return reasoning

  if (!tool_name) return "Thinking…"

  // 2. Template matching
  if (tool_name === "web_search" && args?.query) {
    const q = String(args.query)
    return q.length > 50 ? q.slice(0, 50) + "…" : q
  }
  if (tool_name === "web_fetch" && args?.url) {
    const url = String(args.url)
    return url.length > 50 ? url.slice(0, 50) + "…" : url
  }
  if (tool_name === "python_exec" && typeof args?.code === "string") {
    const lines = args.code.split("\n").length
    return `${lines} lines`
  }
  if (tool_name === "calculator" && args?.expression) {
    return String(args.expression)
  }
  if (tool_name === "file_ops") {
    const op = args?.operation ?? "op"
    const path = args?.path ?? ""
    return `${op} ${path}`
  }
  if ((tool_name === "kb_retrieve" || tool_name === "grounded_retrieve") && args?.query) {
    const q = String(args.query)
    return q.length > 50 ? q.slice(0, 50) + "…" : q
  }
  if (tool_name === "shell_exec" && args?.command) {
    const cmd = String(args.command)
    return cmd.length > 50 ? cmd.slice(0, 50) + "…" : cmd
  }
  if (tool_name === "http_request") {
    const method = args?.method ?? "GET"
    const url = args?.url ? String(args.url) : ""
    const short = url.length > 40 ? url.slice(0, 40) + "…" : url
    return `${method} ${short}`
  }

  // 3. Connector: connector__action → action name
  if (tool_name.includes("__") && !tool_name.startsWith("mcp__")) {
    return tool_name.split("__").slice(1).join(" ")
  }

  // 4. MCP: mcp__service__action → action name
  if (tool_name.startsWith("mcp__")) {
    const parts = tool_name.split("__")
    if (parts.length >= 3) return parts.slice(2).join(" ")
  }

  // 5. Fallback — no summary, display name is enough
  return ""
}
