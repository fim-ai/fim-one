"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import {
  Play,
  Square,
  Brain,
  GitBranch,
  MessageSquareMore,
  Bot,
  Library,
  Plug,
  Globe,
  Variable,
  FileText,
  Code,
  Repeat,
  RefreshCw,
  Combine,
  FileSearch,
  ListFilter,
  ArrowRightLeft,
  FileScan,
  MessageCircleQuestion,
  PanelLeftClose,
  PanelLeftOpen,
  UserCheck,
  Cable,
  Wrench,
  Workflow,
  KeyRound,
} from "lucide-react"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import type { WorkflowNodeType } from "@/types/workflow"

interface NodePaletteItem {
  type: WorkflowNodeType
  icon: React.ReactNode
  color: string
}

interface NodePaletteCategory {
  key: string
  items: NodePaletteItem[]
}

const categories: NodePaletteCategory[] = [
  {
    key: "categoryFlow",
    items: [
      { type: "start", icon: <Play className="h-3.5 w-3.5" />, color: "text-green-500" },
      { type: "end", icon: <Square className="h-3.5 w-3.5" />, color: "text-red-500" },
      { type: "humanIntervention", icon: <UserCheck className="h-3.5 w-3.5" />, color: "text-sky-500" },
      { type: "subWorkflow", icon: <Workflow className="h-3.5 w-3.5" />, color: "text-indigo-500" },
    ],
  },
  {
    key: "categoryAI",
    items: [
      { type: "llm", icon: <Brain className="h-3.5 w-3.5" />, color: "text-blue-500" },
      { type: "questionClassifier", icon: <MessageSquareMore className="h-3.5 w-3.5" />, color: "text-teal-500" },
      { type: "agent", icon: <Bot className="h-3.5 w-3.5" />, color: "text-indigo-500" },
      { type: "knowledgeRetrieval", icon: <Library className="h-3.5 w-3.5" />, color: "text-teal-500" },
      { type: "parameterExtractor", icon: <FileSearch className="h-3.5 w-3.5" />, color: "text-violet-500" },
      { type: "questionUnderstanding", icon: <MessageCircleQuestion className="h-3.5 w-3.5" />, color: "text-pink-500" },
    ],
  },
  {
    key: "categoryLogic",
    items: [
      { type: "conditionBranch", icon: <GitBranch className="h-3.5 w-3.5" />, color: "text-orange-500" },
      { type: "iterator", icon: <Repeat className="h-3.5 w-3.5" />, color: "text-cyan-500" },
      { type: "loop", icon: <RefreshCw className="h-3.5 w-3.5" />, color: "text-orange-500" },
    ],
  },
  {
    key: "categoryIntegration",
    items: [
      { type: "connector", icon: <Plug className="h-3.5 w-3.5" />, color: "text-purple-500" },
      { type: "httpRequest", icon: <Globe className="h-3.5 w-3.5" />, color: "text-slate-500" },
      { type: "mcp", icon: <Cable className="h-3.5 w-3.5" />, color: "text-violet-500" },
      { type: "builtinTool", icon: <Wrench className="h-3.5 w-3.5" />, color: "text-zinc-500" },
    ],
  },
  {
    key: "categoryData",
    items: [
      { type: "variableAssign", icon: <Variable className="h-3.5 w-3.5" />, color: "text-gray-500" },
      { type: "variableAggregator", icon: <Combine className="h-3.5 w-3.5" />, color: "text-sky-500" },
      { type: "templateTransform", icon: <FileText className="h-3.5 w-3.5" />, color: "text-amber-500" },
      { type: "codeExecution", icon: <Code className="h-3.5 w-3.5" />, color: "text-emerald-500" },
      { type: "listOperation", icon: <ListFilter className="h-3.5 w-3.5" />, color: "text-lime-500" },
      { type: "transform", icon: <ArrowRightLeft className="h-3.5 w-3.5" />, color: "text-rose-500" },
      { type: "documentExtractor", icon: <FileScan className="h-3.5 w-3.5" />, color: "text-amber-600" },
      { type: "env", icon: <KeyRound className="h-3.5 w-3.5" />, color: "text-amber-600" },
    ],
  },
]

interface NodePaletteProps {
  existingNodeTypes?: Set<string>
}

export function NodePalette({ existingNodeTypes }: NodePaletteProps) {
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
          placeholder={t("paletteSearch")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-7 text-xs"
        />
      </div>
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-3 pb-3 space-y-3">
          {categories.map((cat) => {
            const filtered = cat.items.filter((item) => {
              if (!searchLower) return true
              const name = t(`nodeType_${item.type}` as Parameters<typeof t>[0]).toLowerCase()
              return name.includes(searchLower) || item.type.toLowerCase().includes(searchLower)
            })
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
        </div>
      </ScrollArea>
    </div>
  )
}
