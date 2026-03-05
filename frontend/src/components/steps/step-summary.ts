import type { LucideIcon } from "lucide-react"
import { Clock, Code2, Globe, FolderOpen, BookOpen, Plug, Server, Wrench } from "lucide-react"
import type { ToolMeta } from "@/hooks/use-tool-catalog"

/* ------------------------------------------------------------------ */
/*  Tool display name                                                   */
/* ------------------------------------------------------------------ */

/** Get a human-friendly display name for a tool, using catalog data when available. */
export function getToolDisplayName(toolName: string, catalog?: ToolMeta[]): string {
  // 1. Catalog lookup
  const meta = catalog?.find((t) => t.name === toolName)
  if (meta) return meta.display_name

  // 2. Connector: connector__action -> title-case connector name
  if (toolName.includes("__") && !toolName.startsWith("mcp__")) {
    return toolName
      .split("__")[0]
      .replace(/_/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  // 3. MCP: mcp__service__action -> title-case service name
  if (toolName.startsWith("mcp__")) {
    const parts = toolName.split("__")
    if (parts.length >= 3) return parts[1].replace(/^\w/, (c) => c.toUpperCase())
  }

  // 4. Fallback: snake_case -> Title Case
  return toolName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

/* ------------------------------------------------------------------ */
/*  Tool icon (category-based)                                          */
/* ------------------------------------------------------------------ */

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  general: Clock,
  computation: Code2,
  web: Globe,
  filesystem: FolderOpen,
  knowledge: BookOpen,
  connector: Plug,
  mcp: Server,
}

/** Get a Lucide icon component for a tool based on its category from the catalog. */
export function getToolIcon(toolName: string, catalog?: ToolMeta[]): LucideIcon {
  const meta = catalog?.find((t) => t.name === toolName)
  if (meta) return CATEGORY_ICONS[meta.category] ?? Wrench

  // Fallback by naming convention
  if (toolName.startsWith("mcp__")) return CATEGORY_ICONS.mcp!
  if (toolName.includes("__")) return CATEGORY_ICONS.connector!
  return Wrench
}

/* ------------------------------------------------------------------ */
/*  Step summary (arg-pattern matching)                                 */
/* ------------------------------------------------------------------ */

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n) + "\u2026" : s)

const ARG_PATTERNS: {
  match: string[]
  fmt: (a: Record<string, unknown>) => string
}[] = [
  { match: ["method", "url"], fmt: (a) => `${a.method} ${trunc(String(a.url), 40)}` },
  { match: ["operation", "path"], fmt: (a) => `${a.operation} ${trunc(String(a.path), 40)}` },
  { match: ["code"], fmt: (a) => `${String(a.code).split("\n").length} lines` },
  { match: ["query"], fmt: (a) => trunc(String(a.query), 50) },
  { match: ["url"], fmt: (a) => trunc(String(a.url), 50) },
  { match: ["command"], fmt: (a) => trunc(String(a.command), 50) },
  { match: ["expression"], fmt: (a) => trunc(String(a.expression), 50) },
]

/**
 * Generate a short English summary for a tool call step.
 * Uses arg-pattern matching -- no hardcoded tool names.
 */
export function generateStepSummary(
  toolName?: string,
  args?: Record<string, unknown>,
  reasoning?: string,
): string {
  // 1. Short reasoning -> use directly
  if (reasoning && reasoning.length < 60) return reasoning

  if (!toolName) return "Thinking\u2026"
  if (!args) return ""

  // 2. Connector: connector__action -> action name
  if (toolName.includes("__") && !toolName.startsWith("mcp__")) {
    return toolName.split("__").slice(1).join(" ")
  }

  // 3. MCP: mcp__service__action -> action name
  if (toolName.startsWith("mcp__")) {
    const parts = toolName.split("__")
    if (parts.length >= 3) return parts.slice(2).join(" ")
  }

  // 4. Arg-pattern matching
  for (const p of ARG_PATTERNS) {
    if (p.match.every((k) => args[k] != null)) return p.fmt(args)
  }

  return ""
}

/* ------------------------------------------------------------------ */
/*  Group title generation                                             */
/* ------------------------------------------------------------------ */

/**
 * Generate a short title for a collapsed iteration group.
 * Tries to extract the first sentence from the final answer;
 * falls back to a "Used N tools in Xs" summary.
 */
export function generateGroupTitle(
  doneAnswer: string,
  toolCallCount: number,
  elapsed: number,
): string {
  // Try to extract first sentence from done answer
  if (doneAnswer) {
    // Strip markdown formatting
    const plain = doneAnswer
      .replace(/[#*_`~\[\]()>]/g, "")
      .replace(/!\[.*?\]\(.*?\)/g, "")
      .replace(/\n+/g, " ")
      .trim()
    // Get first sentence
    const match = plain.match(/^(.+?)[.!?\u3002\uff01\uff1f]\s/)
    const firstSentence = match ? match[1] + match[0].slice(-2, -1) : plain
    if (firstSentence.length > 0 && firstSentence.length <= 80) {
      return firstSentence
    }
    if (firstSentence.length > 80) {
      return firstSentence.slice(0, 77) + "\u2026"
    }
  }
  // Fallback
  const dur = elapsed < 10 ? `${elapsed.toFixed(1)}s` : `${Math.round(elapsed)}s`
  return `Used ${toolCallCount} tool${toolCallCount !== 1 ? "s" : ""} in ${dur}`
}
