"use client"

import { useCallback, useState } from "react"
import { useTranslations, useMessages } from "next-intl"
import { Badge } from "@/components/ui/badge"
import {
  Loader2,
  AlertCircle,
  Wrench,
  Zap,
  Code2,
  Globe,
  FolderOpen,
  BookOpen,
  Image,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { useToolCatalog, type ToolMeta } from "@/hooks/use-tool-catalog"
import { apiFetch } from "@/lib/api"
import { toast } from "sonner"

/* ------------------------------------------------------------------ */
/*  Category icon & color maps                                        */
/* ------------------------------------------------------------------ */

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  general: Zap,
  computation: Code2,
  web: Globe,
  filesystem: FolderOpen,
  knowledge: BookOpen,
  media: Image,
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
/*  SettingSection (mirrors admin-settings.tsx)                        */
/* ------------------------------------------------------------------ */

function SettingSection({
  icon: Icon,
  iconColor,
  title,
  description,
  children,
}: {
  icon: React.ElementType
  iconColor: string
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-sm font-medium flex items-center gap-1.5">
          <Icon className={`h-4 w-4 ${iconColor}`} />
          {title}
        </h4>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <div className="rounded-lg border border-border bg-card p-4">
        {children}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  AdminTools                                                         */
/* ------------------------------------------------------------------ */

export function AdminTools() {
  const t = useTranslations("admin.tools")
  const tTools = useTranslations("tools")
  const messages = useMessages()
  const { data: catalog, isLoading, error, mutate } = useToolCatalog()
  const [togglingTools, setTogglingTools] = useState<Set<string>>(new Set())

  // Safe lookup for builtin tool name/desc translations
  const builtinTranslations = (
    (messages["tools"] as Record<string, unknown>)?.["builtin"] ?? {}
  ) as Record<string, { name?: string; desc?: string }>
  const getToolName = (tool: ToolMeta) =>
    builtinTranslations[tool.name]?.name ?? tool.display_name
  const getToolDesc = (tool: ToolMeta) =>
    builtinTranslations[tool.name]?.desc ?? tool.description

  // Category label helper
  const getCategoryLabel = (key: string) => {
    try {
      return tTools(`categories.${key}` as Parameters<typeof tTools>[0])
    } catch {
      return key.charAt(0).toUpperCase() + key.slice(1)
    }
  }

  // Toggle a tool's disabled state via admin settings API
  const handleToggle = useCallback(
    async (toolName: string, disable: boolean) => {
      if (!catalog) return

      setTogglingTools((prev) => new Set(prev).add(toolName))
      try {
        // Build the new disabled list from catalog
        const currentDisabled = catalog.tools
          .filter((tool) => tool.disabled)
          .map((tool) => tool.name)

        const newDisabled = disable
          ? [...new Set([...currentDisabled, toolName])]
          : currentDisabled.filter((n) => n !== toolName)

        await apiFetch("/api/admin/settings", {
          method: "PATCH",
          body: JSON.stringify({ disabled_builtin_tools: newDisabled }),
        })

        // Optimistically update local SWR cache
        mutate(
          {
            ...catalog,
            tools: catalog.tools.map((tool) =>
              tool.name === toolName ? { ...tool, disabled: disable } : tool
            ),
          },
          false
        )

        toast.success(disable ? t("toolDisabled") : t("toolEnabled"))
      } catch {
        toast.error(
          disable
            ? tTools("failedToDisableTool")
            : tTools("failedToEnableTool")
        )
      } finally {
        setTogglingTools((prev) => {
          const next = new Set(prev)
          next.delete(toolName)
          return next
        })
      }
    },
    [catalog, mutate, t, tTools]
  )

  // Filter out connector and mcp tools (they have their own admin pages)
  const builtinTools =
    catalog?.tools.filter(
      (tool) => tool.category !== "connector" && tool.category !== "mcp"
    ) ?? []

  // Group tools by category
  const grouped = builtinTools.reduce<Record<string, ToolMeta[]>>(
    (acc, tool) => {
      const key = tool.category
      if (!acc[key]) acc[key] = []
      acc[key].push(tool)
      return acc
    },
    {}
  )

  // Stable category order: use catalog categories minus connector/mcp
  const categoryOrder = (catalog?.categories ?? []).filter(
    (c) => c !== "connector" && c !== "mcp"
  )

  return (
    <div className="space-y-8 max-w-4xl">
      <div>
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">{tTools("loadingTools")}</span>
        </div>
      )}

      {/* Error state */}
      {error && !isLoading && (
        <div className="flex items-center justify-center gap-2 py-8 text-destructive">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">{tTools("failedToLoadCatalog")}</span>
        </div>
      )}

      {/* Tool groups */}
      {!isLoading &&
        !error &&
        categoryOrder.map((category) => {
          const tools = grouped[category]
          if (!tools || tools.length === 0) return null
          const CategoryIcon = CATEGORY_ICONS[category] ?? Wrench
          const iconColor = CATEGORY_COLORS[category] ?? "text-muted-foreground"

          return (
            <SettingSection
              key={category}
              icon={CategoryIcon}
              iconColor={iconColor}
              title={getCategoryLabel(category)}
              description={`${tools.length} tool${tools.length > 1 ? "s" : ""}`}
            >
              <div className="grid grid-cols-1 gap-3">
                {tools.map((tool) => {
                  const isDisabled = tool.disabled === true
                  const isToggling = togglingTools.has(tool.name)
                  const Icon = CATEGORY_ICONS[tool.category] ?? Wrench
                  const toolIconColor = isDisabled
                    ? "text-muted-foreground/40"
                    : (CATEGORY_COLORS[tool.category] ?? "text-muted-foreground")

                  return (
                    <div
                      key={tool.name}
                      className={`flex items-start justify-between gap-4 rounded-md border p-3 transition-colors ${
                        isDisabled
                          ? "border-border/50 bg-muted/30"
                          : "border-border bg-background"
                      }`}
                    >
                      {/* Left: info */}
                      <div className="flex items-start gap-3 min-w-0 flex-1">
                        <Icon
                          className={`h-4 w-4 shrink-0 mt-0.5 ${toolIconColor}`}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span
                              className={`text-sm font-medium ${
                                isDisabled ? "text-muted-foreground" : ""
                              }`}
                            >
                              {getToolName(tool)}
                            </span>
                            <Badge
                              variant="secondary"
                              className="text-xs font-mono"
                            >
                              {tool.name}
                            </Badge>
                          </div>
                          <p
                            className={`text-xs leading-relaxed mt-1 ${
                              isDisabled
                                ? "text-muted-foreground/60"
                                : "text-muted-foreground"
                            }`}
                          >
                            {getToolDesc(tool)}
                          </p>
                        </div>
                      </div>

                      {/* Right: toggle + badge */}
                      <div className="flex items-center gap-2 shrink-0 pt-0.5">
                        <Badge
                          variant="outline"
                          className={`text-xs ${
                            isDisabled
                              ? "text-destructive border-destructive/30"
                              : "text-green-600 border-green-500/30"
                          }`}
                        >
                          {isDisabled ? t("disabled") : t("enabled")}
                        </Badge>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={!isDisabled}
                          onClick={() =>
                            handleToggle(tool.name, !isDisabled)
                          }
                          disabled={isToggling}
                          className={`relative shrink-0 inline-flex h-5 w-9 items-center rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary ${
                            isToggling ? "opacity-50 cursor-wait" : ""
                          } ${
                            isDisabled
                              ? "bg-muted-foreground/30"
                              : "bg-green-500"
                          }`}
                        >
                          <span
                            className={`inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform ${
                              isDisabled
                                ? "translate-x-0.5"
                                : "translate-x-[18px]"
                            }`}
                          />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </SettingSection>
          )
        })}
    </div>
  )
}
