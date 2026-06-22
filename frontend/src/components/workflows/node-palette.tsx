"use client"

import { useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import {
  Play,
  Square,
  Brain,
  GitBranch,
  Bot,
  Library,
  Plug,
  Clock,
  PanelLeftClose,
  PanelLeftOpen,
  UserCheck,
  Cable,
} from "lucide-react"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { WorkflowNodeType } from "@/types/workflow"

export interface NodePaletteItem {
  type: WorkflowNodeType
  icon: React.ReactNode
  color: string
}

export interface NodePaletteCategory {
  key: string
  items: NodePaletteItem[]
}

export const categories: NodePaletteCategory[] = [
  {
    key: "categoryFlow",
    items: [
      { type: "start", icon: <Play className="h-3.5 w-3.5" />, color: "text-green-500" },
      { type: "end", icon: <Square className="h-3.5 w-3.5" />, color: "text-red-500" },
      { type: "humanIntervention", icon: <UserCheck className="h-3.5 w-3.5" />, color: "text-sky-500" },
    ],
  },
  {
    key: "categoryAI",
    items: [
      { type: "llm", icon: <Brain className="h-3.5 w-3.5" />, color: "text-blue-500" },
      { type: "agent", icon: <Bot className="h-3.5 w-3.5" />, color: "text-indigo-500" },
      { type: "knowledgeRetrieval", icon: <Library className="h-3.5 w-3.5" />, color: "text-teal-500" },
    ],
  },
  {
    key: "categoryLogic",
    items: [
      { type: "conditionBranch", icon: <GitBranch className="h-3.5 w-3.5" />, color: "text-orange-500" },
    ],
  },
  {
    key: "categoryIntegration",
    items: [
      { type: "connector", icon: <Plug className="h-3.5 w-3.5" />, color: "text-purple-500" },
      { type: "mcp", icon: <Cable className="h-3.5 w-3.5" />, color: "text-violet-500" },
    ],
  },
]

/** Flat lookup from node type to its palette item (icon + color) */
export const itemByType: Record<WorkflowNodeType, NodePaletteItem> = Object.fromEntries(
  categories.flatMap((cat) => cat.items.map((item) => [item.type, item])),
) as Record<WorkflowNodeType, NodePaletteItem>

interface NodePaletteProps {
  existingNodeTypes?: Set<string>
  recentNodes?: WorkflowNodeType[]
}

export function NodePalette({ existingNodeTypes, recentNodes = [] }: NodePaletteProps) {
  const t = useTranslations("workflows")
  const [search, setSearch] = useState("")
  const [collapsed, setCollapsed] = useState(false)

  const handleDragStart = (
    event: React.DragEvent,
    nodeType: WorkflowNodeType,
  ) => {
    event.dataTransfer.setData("application/reactflow-node-type", nodeType)
    event.dataTransfer.effectAllowed = "move"
  }

  const searchLower = search.toLowerCase()

  /** Check whether a node item matches the current search query */
  const matchesSearch = (item: NodePaletteItem): boolean => {
    if (!searchLower) return true
    const name = t(`nodeType_${item.type}` as Parameters<typeof t>[0]).toLowerCase()
    const desc = t(`nodeDesc_${item.type}` as Parameters<typeof t>[0]).toLowerCase()
    return (
      name.includes(searchLower) ||
      desc.includes(searchLower) ||
      item.type.toLowerCase().includes(searchLower)
    )
  }

  /** Filtered recently used items */
  const filteredRecent = useMemo(() => {
    return recentNodes
      .filter((type) => itemByType[type] != null)
      .map((type) => itemByType[type])
      .filter(matchesSearch)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentNodes, searchLower])

  /** Whether ANY category or recent section has results */
  const hasAnyResults = useMemo(() => {
    if (filteredRecent.length > 0) return true
    return categories.some((cat) => cat.items.some(matchesSearch))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchLower, filteredRecent.length])

  const isSingletonDisabled = (type: WorkflowNodeType) => {
    if (type === "start" && existingNodeTypes?.has("start")) return true
    if (type === "end" && existingNodeTypes?.has("end")) return true
    return false
  }

  // Collapsed view: show only icons
  if (collapsed) {
    return (
      <div className="flex flex-col h-full border-r border-border/40 bg-background w-[48px] items-center">
        <div className="pt-2 pb-2 shrink-0">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setCollapsed(false)}
            className="h-7 w-7"
          >
            <PanelLeftOpen className="h-3.5 w-3.5" />
          </Button>
        </div>
        <ScrollArea className="flex-1 min-h-0 w-full">
          <div className="flex flex-col items-center gap-0.5 pb-2">
            {categories.flatMap((cat) =>
              cat.items.map((item) => {
                const disabled = isSingletonDisabled(item.type)
                return (
                  <Tooltip key={item.type}>
                    <TooltipTrigger asChild>
                      <div
                        draggable={!disabled}
                        onDragStart={(e) => {
                          if (disabled) {
                            e.preventDefault()
                            return
                          }
                          handleDragStart(e, item.type)
                        }}
                        className={cn(
                          "flex h-8 w-8 items-center justify-center rounded-md transition-colors",
                          disabled
                            ? "opacity-30 cursor-not-allowed"
                            : "cursor-grab active:cursor-grabbing hover:bg-accent/50",
                          item.color,
                        )}
                      >
                        {item.icon}
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="right" className="text-xs">
                      {t(`nodeType_${item.type}` as Parameters<typeof t>[0])}
                    </TooltipContent>
                  </Tooltip>
                )
              }),
            )}
          </div>
        </ScrollArea>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full border-r border-border/40 bg-background w-[220px]">
      <div className="flex items-center justify-between px-3 pt-3 pb-2 shrink-0">
        <h3 className="text-xs font-semibold text-foreground">
          {t("paletteTitle")}
        </h3>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setCollapsed(true)}
          className="h-6 w-6"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </Button>
      </div>
      <div className="px-3 pb-2 shrink-0">
        <Input
          placeholder={t("searchNodes")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-7 text-xs"
        />
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-3 pb-3 space-y-3">
          {/* Recently Used section */}
          {filteredRecent.length > 0 && (
            <div>
              <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider mb-1 flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {t("recentlyUsed")}
              </p>
              <div className="space-y-0.5">
                {filteredRecent.map((item) => {
                  const disabled = isSingletonDisabled(item.type)
                  return (
                    <div
                      key={`recent-${item.type}`}
                      draggable={!disabled}
                      onDragStart={(e) => {
                        if (disabled) { e.preventDefault(); return }
                        handleDragStart(e, item.type)
                      }}
                      className={cn(
                        "flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
                        disabled
                          ? "opacity-30 cursor-not-allowed"
                          : "cursor-grab active:cursor-grabbing hover:bg-accent/50",
                      )}
                    >
                      <div className={cn("shrink-0", item.color)}>
                        {item.icon}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs font-medium text-foreground truncate">
                          {t(`nodeType_${item.type}` as Parameters<typeof t>[0])}
                        </p>
                        <p className="text-[10px] text-muted-foreground/60 truncate">
                          {t(`nodeDesc_${item.type}` as Parameters<typeof t>[0])}
                        </p>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Category sections */}
          {categories.map((cat) => {
            const filtered = cat.items.filter(matchesSearch)
            if (filtered.length === 0) return null
            return (
              <div key={cat.key}>
                <p className="text-[10px] font-semibold text-muted-foreground/70 uppercase tracking-wider mb-1.5 border-b border-border/30 pb-1">
                  {t(cat.key as Parameters<typeof t>[0])}
                </p>
                <div className="space-y-0.5">
                  {filtered.map((item) => {
                    const disabled = isSingletonDisabled(item.type)
                    return (
                      <div
                        key={item.type}
                        draggable={!disabled}
                        onDragStart={(e) => {
                          if (disabled) {
                            e.preventDefault()
                            return
                          }
                          handleDragStart(e, item.type)
                        }}
                        className={cn(
                          "flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
                          disabled
                            ? "opacity-30 cursor-not-allowed"
                            : "cursor-grab active:cursor-grabbing hover:bg-accent/50",
                        )}
                      >
                        <div className={cn("shrink-0", item.color)}>
                          {item.icon}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-foreground truncate">
                            {t(`nodeType_${item.type}` as Parameters<typeof t>[0])}
                          </p>
                          <p className="text-[10px] text-muted-foreground/60 truncate">
                            {t(`nodeDesc_${item.type}` as Parameters<typeof t>[0])}
                          </p>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}

          {/* No results message */}
          {!hasAnyResults && (
            <p className="text-xs text-muted-foreground text-center py-4">
              {t("noNodeSearchResults")}
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}
