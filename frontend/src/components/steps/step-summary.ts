/**
 * Generate a human-readable Chinese summary for a tool call step.
 * Pure template-based approach — no LLM needed.
 */
export function generateStepSummary(
  tool_name?: string,
  args?: Record<string, unknown>,
  reasoning?: string,
): string {
  // 1. Short reasoning → use directly
  if (reasoning && reasoning.length < 80) return reasoning

  if (!tool_name) return "思考中"

  // 2. Template matching by tool name
  if (tool_name === "web_search" && args?.query) {
    return `搜索「${args.query}」`
  }
  if (tool_name === "web_fetch" && args?.url) {
    const url = String(args.url)
    const short = url.length > 40 ? url.slice(0, 40) + "…" : url
    return `获取网页 ${short}`
  }
  if (tool_name === "python_exec" && typeof args?.code === "string") {
    const lines = args.code.split("\n").length
    return `执行 Python 代码 (${lines} 行)`
  }
  if (tool_name === "calculator" && args?.expression) {
    return `计算 ${args.expression}`
  }
  if (tool_name === "file_ops") {
    const op = args?.operation ?? "操作"
    const path = args?.path ?? ""
    return `文件操作: ${op} ${path}`
  }
  if (tool_name === "kb_retrieve" && args?.query) {
    return `检索知识库「${args.query}」`
  }
  if (tool_name === "grounded_retrieve" && args?.query) {
    return `检索知识库「${args.query}」`
  }
  if (tool_name === "shell_exec" && args?.command) {
    const cmd = String(args.command)
    const short = cmd.length > 40 ? cmd.slice(0, 40) + "…" : cmd
    return `执行命令 ${short}`
  }
  if (tool_name === "http_request") {
    const method = args?.method ?? "GET"
    const url = args?.url ? String(args.url) : ""
    const short = url.length > 30 ? url.slice(0, 30) + "…" : url
    return `${method} ${short}`
  }

  // 3. Connector pattern: {connector}__{action}
  if (tool_name.includes("__")) {
    const parts = tool_name.split("__")
    if (parts.length === 2) {
      return `${parts[0]}: ${parts[1]}`
    }
  }

  // 4. MCP pattern: mcp__service__action
  if (tool_name.startsWith("mcp__")) {
    const parts = tool_name.split("__")
    if (parts.length >= 3) {
      return `${parts[1]}: ${parts.slice(2).join("_")}`
    }
  }

  // 5. Fallback
  return `调用 ${tool_name}`
}
