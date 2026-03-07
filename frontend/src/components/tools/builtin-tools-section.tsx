"use client"

import { useState } from "react"
import Link from "next/link"
import { useTranslations } from "next-intl"
import { Badge } from "@/components/ui/badge"
import {
  Zap,
  Code2,
  Globe,
  FolderOpen,
  BookOpen,
  Image,
  Plug,
  Server,
  Wrench,
  ArrowRight,
  Loader2,
  AlertCircle,
  Lock,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useToolCatalog } from "@/hooks/use-tool-catalog"
import type { ToolMeta } from "@/hooks/use-tool-catalog"

/* ------------------------------------------------------------------ */
/*  Category icon & color maps                                          */
/* ------------------------------------------------------------------ */

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  general: Zap,
  computation: Code2,
  web: Globe,
  filesystem: FolderOpen,
  knowledge: BookOpen,
  media: Image,
  connector: Plug,
  mcp: Server,
}

const CATEGORY_COLORS: Record<string, string> = {
  general: "text-yellow-500",
  computation: "text-blue-500",
  web: "text-green-500",
  filesystem: "text-orange-500",
  knowledge: "text-purple-500",
  media: "text-pink-500",
}

/* ------------------------------------------------------------------ */
/*  ToolCard                                                            */
/* ------------------------------------------------------------------ */

function ToolCard({ tool, notConfiguredLabel }: { tool: ToolMeta; notConfiguredLabel: string }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = CATEGORY_ICONS[tool.category] ?? Wrench
  const categoryLabel = tool.category.charAt(0).toUpperCase() + tool.category.slice(1)
  const unavailable = tool.available === false

  const iconColor = unavailable
    ? "text-muted-foreground/40"
    : (CATEGORY_COLORS[tool.category] ?? "text-muted-foreground")

  return (
    <div
      className={`rounded-lg border bg-card p-4 flex flex-col gap-2 transition-colors ${
        unavailable
          ? "border-border/50 opacity-60 cursor-default"
          : "border-border hover:border-border/80 hover:bg-accent/5 cursor-pointer"
      }`}
      onClick={() => !unavailable && setExpanded((v) => !v)}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
          <span className={`text-sm font-medium shrink-0 ${unavailable ? "text-muted-foreground" : ""}`}>
            {tool.display_name}
          </span>
          <Badge variant="secondary" className="shrink-0 text-xs font-mono">
            {tool.name}
          </Badge>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {unavailable && (
            <div title={tool.unavailable_reason ?? notConfiguredLabel}>
              <Lock className="h-3 w-3 text-muted-foreground/50" />
            </div>
          )}
          <Badge variant="outline" className="text-xs text-muted-foreground">
            {categoryLabel}
          </Badge>
        </div>
      </div>
      <p className={`text-xs leading-relaxed ${unavailable ? "text-muted-foreground/60" : "text-muted-foreground"} ${expanded ? "" : "line-clamp-2"}`}>
        {unavailable && tool.unavailable_reason
          ? tool.unavailable_reason
          : tool.description}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Navigation link cards (unchanged)                                   */
/* ------------------------------------------------------------------ */

function ConnectorLinkCard({ label, description }: { label: string; description: string }) {
  return (
    <Link href="/connectors" className="block group">
      <div className="rounded-lg border border-dashed border-border bg-card p-4 flex flex-col gap-2 hover:border-primary/50 hover:bg-accent/30 transition-colors">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Plug className="h-4 w-4 shrink-0 text-cyan-500" />
            <span className="text-sm font-medium shrink-0">{label}</span>
            <Badge variant="secondary" className="shrink-0 text-xs font-mono">
              connector
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
              {label}
            </Badge>
            <ArrowRight className="h-3 w-3 text-muted-foreground/50" />
          </div>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
          {description}
        </p>
      </div>
    </Link>
  )
}

function MCPLinkCard({ onSwitch, label, description }: { onSwitch: () => void; label: string; description: string }) {
  return (
    <button onClick={onSwitch} className="text-left w-full group">
      <div className="rounded-lg border border-dashed border-border bg-card p-4 flex flex-col gap-2 hover:border-primary/50 hover:bg-accent/30 transition-colors">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Server className="h-4 w-4 shrink-0 text-indigo-500" />
            <span className="text-sm font-medium shrink-0">{label}</span>
            <Badge variant="secondary" className="shrink-0 text-xs font-mono">
              mcp
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
              MCP
            </Badge>
            <ArrowRight className="h-3 w-3 text-muted-foreground/50" />
          </div>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
          {description}
        </p>
      </div>
    </button>
  )
}

/* ------------------------------------------------------------------ */
/*  Main section                                                        */
/* ------------------------------------------------------------------ */

interface BuiltinToolsSectionProps {
  onSwitchToMCP: () => void
}

export function BuiltinToolsSection({ onSwitchToMCP }: BuiltinToolsSectionProps) {
  const t = useTranslations("tools")
  const { data: catalog, isLoading, error } = useToolCatalog()
  const [activeCategory, setActiveCategory] = useState<string>("All")

  // Derive categories from catalog, with "All" prepended, and Connector/MCP appended
  const apiCategories = catalog?.categories ?? []
  // Exclude connector and mcp from tool categories (they have dedicated link cards)
  const toolCategories = apiCategories.filter((c) => c !== "connector" && c !== "mcp")
  const categories = ["All", ...toolCategories.map((c) => c.charAt(0).toUpperCase() + c.slice(1)), "Connector", "MCP"]

  // Filter tools from catalog (exclude connector/mcp tools — those come from link cards)
  const allTools = catalog?.tools.filter((t) => t.category !== "connector" && t.category !== "mcp") ?? []

  const showConnector = activeCategory === "All" || activeCategory === "Connector"
  const showMCP = activeCategory === "All" || activeCategory === "MCP"

  const filteredTools =
    activeCategory === "All"
      ? allTools
      : activeCategory === "Connector" || activeCategory === "MCP"
        ? []
        : allTools.filter((t) => t.category === activeCategory.toLowerCase())

  return (
    <div className="flex flex-col gap-4">
      {/* Category filter chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              activeCategory === cat
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground"
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">{t("loadingTools")}</span>
        </div>
      )}

      {/* Error state */}
      {error && !isLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-destructive">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">{t("failedToLoadCatalog")}</span>
        </div>
      )}

      {/* Tool cards grid */}
      {!isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filteredTools.map((tool) => (
            <ToolCard key={tool.name} tool={tool} notConfiguredLabel={t("notConfigured")} />
          ))}
          {showConnector && <ConnectorLinkCard label={t("connectorLabel")} description={t("connectorDescription")} />}
          {showMCP && <MCPLinkCard onSwitch={onSwitchToMCP} label={t("mcpServersLinkLabel")} description={t("mcpServersLinkDescription")} />}
        </div>
      )}
    </div>
  )
}
