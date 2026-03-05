"use client"

import { useState } from "react"
import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import {
  Clock,
  Code2,
  Globe,
  FolderOpen,
  BookOpen,
  Plug,
  Server,
  Wrench,
  ArrowRight,
  Loader2,
  AlertCircle,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useToolCatalog } from "@/hooks/use-tool-catalog"
import type { ToolMeta } from "@/hooks/use-tool-catalog"

/* ------------------------------------------------------------------ */
/*  Category icon & color maps                                          */
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

const CATEGORY_COLORS: Record<string, string> = {
  general: "text-muted-foreground",
  computation: "text-blue-500",
  web: "text-green-500",
  filesystem: "text-orange-500",
  knowledge: "text-purple-500",
}

/* ------------------------------------------------------------------ */
/*  ToolCard                                                            */
/* ------------------------------------------------------------------ */

function ToolCard({ tool }: { tool: ToolMeta }) {
  const Icon = CATEGORY_ICONS[tool.category] ?? Wrench
  const iconColor = CATEGORY_COLORS[tool.category] ?? "text-muted-foreground"
  const categoryLabel = tool.category.charAt(0).toUpperCase() + tool.category.slice(1)

  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-2 transition-colors hover:border-border/80 hover:bg-accent/5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
          <span className="text-sm font-medium shrink-0">{tool.display_name}</span>
          <Badge variant="secondary" className="shrink-0 text-xs font-mono">
            {tool.name}
          </Badge>
        </div>
        <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
          {categoryLabel}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground leading-relaxed">
        {tool.description}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Navigation link cards (unchanged)                                   */
/* ------------------------------------------------------------------ */

function ConnectorLinkCard() {
  return (
    <Link href="/connectors" className="block group">
      <div className="rounded-lg border border-dashed border-border bg-card p-4 flex flex-col gap-2 hover:border-primary/50 hover:bg-accent/30 transition-colors">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Plug className="h-4 w-4 shrink-0 text-cyan-500" />
            <Badge variant="secondary" className="shrink-0 text-xs font-mono">
              connector
            </Badge>
          </div>
          <div className="flex items-center gap-1">
            <Badge variant="outline" className="shrink-0 text-xs text-muted-foreground">
              Connector
            </Badge>
            <ArrowRight className="h-3 w-3 text-muted-foreground/50" />
          </div>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Custom HTTP API actions bound to this agent. Managed on the Connectors page.
        </p>
      </div>
    </Link>
  )
}

function MCPLinkCard({ onSwitch }: { onSwitch: () => void }) {
  return (
    <button onClick={onSwitch} className="text-left w-full group">
      <div className="rounded-lg border border-dashed border-border bg-card p-4 flex flex-col gap-2 hover:border-primary/50 hover:bg-accent/30 transition-colors">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Server className="h-4 w-4 shrink-0 text-indigo-500" />
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
        <p className="text-xs text-muted-foreground leading-relaxed">
          Tools provided by external MCP servers. Configure them in the MCP Servers tab.
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
          <span className="text-sm">Loading tools...</span>
        </div>
      )}

      {/* Error state */}
      {error && !isLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-destructive">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">Failed to load tool catalog</span>
        </div>
      )}

      {/* Tool cards grid */}
      {!isLoading && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filteredTools.map((tool) => (
            <ToolCard key={tool.name} tool={tool} />
          ))}
          {showConnector && <ConnectorLinkCard />}
          {showMCP && <MCPLinkCard onSwitch={onSwitchToMCP} />}
        </div>
      )}
    </div>
  )
}
