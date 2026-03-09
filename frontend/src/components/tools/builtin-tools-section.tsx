"use client"

import { useState } from "react"
import Link from "next/link"
import { useTranslations, useMessages } from "next-intl"
import { Badge } from "@/components/ui/badge"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
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
  Ban,
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

interface ToolCardProps {
  tool: ToolMeta
  notConfiguredLabel: string
  toolName: string
  toolDesc: string
  categoryLabel: string
  isDisabled: boolean
}

function ToolCard({
  tool,
  notConfiguredLabel,
  toolName,
  toolDesc,
  categoryLabel,
  isDisabled,
}: ToolCardProps) {
  const [expanded, setExpanded] = useState(false)
  const t = useTranslations("tools")
  const Icon = CATEGORY_ICONS[tool.category] ?? Wrench
  const unavailable = tool.available === false
  const dimmed = unavailable || isDisabled

  const iconColor = dimmed
    ? "text-muted-foreground/40"
    : (CATEGORY_COLORS[tool.category] ?? "text-muted-foreground")

  return (
    <div
      className={`rounded-lg border bg-card p-4 flex flex-col gap-2 transition-colors ${
        dimmed
          ? "border-border/50 opacity-60 cursor-default"
          : "border-border hover:border-border/80 hover:bg-accent/5 cursor-pointer"
      }`}
      onClick={() => !dimmed && setExpanded((v) => !v)}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className={`h-4 w-4 shrink-0 ${iconColor}`} />
          <span className={`text-sm font-medium shrink-0 ${dimmed ? "text-muted-foreground" : ""}`}>
            {toolName}
          </span>
          <Badge variant="secondary" className="shrink-0 text-xs font-mono">
            {tool.name}
          </Badge>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {isDisabled && (
            <Tooltip>
              <TooltipTrigger asChild>
                <div>
                  <Ban className="h-3 w-3 text-muted-foreground/50" />
                </div>
              </TooltipTrigger>
              <TooltipContent side="bottom" sideOffset={5}>
                {t("disabledByAdmin")}
              </TooltipContent>
            </Tooltip>
          )}
          {unavailable && !isDisabled && (
            <div title={tool.unavailable_reason ?? notConfiguredLabel}>
              <Lock className="h-3 w-3 text-muted-foreground/50" />
            </div>
          )}
          {!isDisabled && (
            <Badge variant="outline" className="text-xs text-muted-foreground">
              {categoryLabel}
            </Badge>
          )}
        </div>
      </div>
      <p className={`text-xs leading-relaxed ${dimmed ? "text-muted-foreground/60" : "text-muted-foreground"} ${expanded ? "" : "line-clamp-2"}`}>
        {isDisabled
          ? t("disabledByAdmin")
          : unavailable && tool.unavailable_reason
            ? tool.unavailable_reason
            : toolDesc}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Navigation link cards                                               */
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

function MCPLinkCard({ label, description }: { label: string; description: string }) {
  return (
    <Link href="/connectors?tab=mcp" className="block group">
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
    </Link>
  )
}

/* ------------------------------------------------------------------ */
/*  Main section                                                        */
/* ------------------------------------------------------------------ */

export function BuiltinToolsSection() {
  const t = useTranslations("tools")
  const messages = useMessages()
  const { data: catalog, isLoading, error } = useToolCatalog()
  const [activeCategory, setActiveCategory] = useState<string>("all")

  // Safe lookup for builtin tool name/desc translations
  const builtinTranslations = ((messages["tools"] as Record<string, unknown>)?.["builtin"] ?? {}) as Record<
    string,
    { name?: string; desc?: string }
  >
  const getToolName = (tool: ToolMeta) => builtinTranslations[tool.name]?.name ?? tool.display_name
  const getToolDesc = (tool: ToolMeta) => builtinTranslations[tool.name]?.desc ?? tool.description

  // Category label helper — falls back to title-cased key if translation missing
  const getCategoryLabel = (key: string) => {
    try {
      return t(`categories.${key}` as Parameters<typeof t>[0])
    } catch {
      return key.charAt(0).toUpperCase() + key.slice(1)
    }
  }

  // Track disabled tools from the catalog
  const disabledToolNames = new Set(
    catalog?.tools.filter((tool) => tool.disabled).map((tool) => tool.name) ?? []
  )

  // Derive categories from catalog; exclude connector/mcp (they have dedicated link cards)
  const apiCategories = catalog?.categories ?? []
  const toolCategories = apiCategories.filter((c) => c !== "connector" && c !== "mcp")
  const categoryKeys = ["all", ...toolCategories, "connector", "mcp"]

  // Filter tools from catalog (exclude connector/mcp tools)
  const allTools = catalog?.tools.filter((t) => t.category !== "connector" && t.category !== "mcp") ?? []

  const showConnector = activeCategory === "all" || activeCategory === "connector"
  const showMCP = activeCategory === "all" || activeCategory === "mcp"

  const filteredTools =
    activeCategory === "all"
      ? allTools
      : activeCategory === "connector" || activeCategory === "mcp"
        ? []
        : allTools.filter((tool) => tool.category === activeCategory)

  return (
    <div className="flex flex-col gap-4">
      {/* Category filter chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {categoryKeys.map((key) => (
          <button
            key={key}
            onClick={() => setActiveCategory(key)}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
              activeCategory === key
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground"
            }`}
          >
            {getCategoryLabel(key)}
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
            <ToolCard
              key={tool.name}
              tool={tool}
              notConfiguredLabel={t("notConfigured")}
              toolName={getToolName(tool)}
              toolDesc={getToolDesc(tool)}
              categoryLabel={getCategoryLabel(tool.category)}
              isDisabled={disabledToolNames.has(tool.name)}
            />
          ))}
          {showConnector && <ConnectorLinkCard label={t("connectorLabel")} description={t("connectorDescription")} />}
          {showMCP && <MCPLinkCard label={t("mcpServersLinkLabel")} description={t("mcpServersLinkDescription")} />}
        </div>
      )}
    </div>
  )
}
