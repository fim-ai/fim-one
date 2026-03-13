"use client"

import { useState, useCallback, useMemo } from "react"
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
  Braces,
  ListFilter,
  ArrowRightLeft,
  FileScan,
  MessageCircleQuestion,
  UserCheck,
  Cable,
  Wrench,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
} from "@/components/ui/command"
import { cn } from "@/lib/utils"
import type { Node } from "@xyflow/react"
import type { WorkflowNodeType } from "@/types/workflow"

// --- Icon map for node types ---

const nodeTypeIcons: Record<WorkflowNodeType, React.ReactNode> = {
  start: <Play className="h-3 w-3" />,
  end: <Square className="h-3 w-3" />,
  llm: <Brain className="h-3 w-3" />,
  conditionBranch: <GitBranch className="h-3 w-3" />,
  questionClassifier: <MessageSquareMore className="h-3 w-3" />,
  agent: <Bot className="h-3 w-3" />,
  knowledgeRetrieval: <Library className="h-3 w-3" />,
  connector: <Plug className="h-3 w-3" />,
  httpRequest: <Globe className="h-3 w-3" />,
  variableAssign: <Variable className="h-3 w-3" />,
  templateTransform: <FileText className="h-3 w-3" />,
  codeExecution: <Code className="h-3 w-3" />,
  iterator: <Repeat className="h-3 w-3" />,
  loop: <RefreshCw className="h-3 w-3" />,
  variableAggregator: <Combine className="h-3 w-3" />,
  parameterExtractor: <FileSearch className="h-3 w-3" />,
  listOperation: <ListFilter className="h-3 w-3" />,
  transform: <ArrowRightLeft className="h-3 w-3" />,
  documentExtractor: <FileScan className="h-3 w-3" />,
  questionUnderstanding: <MessageCircleQuestion className="h-3 w-3" />,
  humanIntervention: <UserCheck className="h-3 w-3" />,
  mcp: <Cable className="h-3 w-3" />,
  builtinTool: <Wrench className="h-3 w-3" />,
}

const nodeTypeColors: Record<WorkflowNodeType, string> = {
  start: "text-green-500",
  end: "text-red-500",
  llm: "text-blue-500",
  conditionBranch: "text-orange-500",
  questionClassifier: "text-teal-500",
  agent: "text-indigo-500",
  knowledgeRetrieval: "text-teal-500",
  connector: "text-purple-500",
  httpRequest: "text-slate-500",
  variableAssign: "text-gray-500",
  templateTransform: "text-amber-500",
  codeExecution: "text-emerald-500",
  iterator: "text-cyan-500",
  loop: "text-orange-500",
  variableAggregator: "text-sky-500",
  parameterExtractor: "text-violet-500",
  listOperation: "text-lime-500",
  transform: "text-rose-500",
  documentExtractor: "text-amber-600",
  questionUnderstanding: "text-pink-500",
  humanIntervention: "text-sky-500",
  mcp: "text-violet-500",
  builtinTool: "text-zinc-500",
}

// --- Helper: extract output variables from a node ---

export interface NodeVariable {
  nodeId: string
  nodeType: WorkflowNodeType
  variableName: string
  /** Full reference string: {{nodeId.variableName}} */
  reference: string
}

/**
 * Extracts all output variable names from a node based on its type and data.
 */
export function getNodeOutputVariables(node: Node): NodeVariable[] {
  const nodeType = node.type as WorkflowNodeType
  const data = node.data as Record<string, unknown>
  const nodeId = node.id
  const variables: NodeVariable[] = []

  switch (nodeType) {
    case "start": {
      const vars = (data.variables ?? []) as Array<{ name: string }>
      for (const v of vars) {
        if (v.name) {
          variables.push({
            nodeId,
            nodeType,
            variableName: v.name,
            reference: `{{${nodeId}.${v.name}}}`,
          })
        }
      }
      break
    }

    case "variableAssign": {
      const assignments = (data.assignments ?? []) as Array<{ variable: string }>
      for (const a of assignments) {
        if (a.variable) {
          variables.push({
            nodeId,
            nodeType,
            variableName: a.variable,
            reference: `{{${nodeId}.${a.variable}}}`,
          })
        }
      }
      break
    }

    // All other node types that have a single output_variable
    case "llm":
    case "agent":
    case "knowledgeRetrieval":
    case "connector":
    case "httpRequest":
    case "templateTransform":
    case "codeExecution":
    case "listOperation":
    case "transform":
    case "documentExtractor":
    case "questionUnderstanding":
    case "mcp":
    case "builtinTool":
    case "humanIntervention": {
      const outputVar = (data.output_variable ?? "") as string
      if (outputVar) {
        variables.push({
          nodeId,
          nodeType,
          variableName: outputVar,
          reference: `{{${nodeId}.${outputVar}}}`,
        })
      }
      break
    }

    // end, conditionBranch, questionClassifier have no output variables
    default:
      break
  }

  return variables
}

// --- VariablePicker component ---

interface VariablePickerProps {
  /** All nodes available as variable sources (should exclude current node) */
  sourceNodes: Node[]
  /** Called when user selects a variable. Receives the full reference string like {{node_id.var}} */
  onInsert: (reference: string) => void
  /** Optional: side of the popover */
  side?: "top" | "bottom" | "left" | "right"
  /** Optional: alignment of the popover */
  align?: "start" | "center" | "end"
}

export function VariablePicker({ sourceNodes, onInsert, side = "bottom", align = "start" }: VariablePickerProps) {
  const t = useTranslations("workflows")
  const [open, setOpen] = useState(false)

  // Group variables by source node
  const groupedVariables = useMemo(() => {
    const groups: Array<{
      nodeId: string
      nodeType: WorkflowNodeType
      variables: NodeVariable[]
    }> = []

    for (const node of sourceNodes) {
      const vars = getNodeOutputVariables(node)
      if (vars.length > 0) {
        groups.push({
          nodeId: node.id,
          nodeType: node.type as WorkflowNodeType,
          variables: vars,
        })
      }
    }

    return groups
  }, [sourceNodes])

  const handleSelect = useCallback(
    (reference: string) => {
      onInsert(reference)
      setOpen(false)
    },
    [onInsert],
  )

  const hasVariables = groupedVariables.length > 0

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="h-6 w-6 shrink-0"
          aria-label={t("variablePickerLabel")}
        >
          <Braces className="h-3 w-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side={side}
        align={align}
        className="w-[260px] p-0"
      >
        <Command>
          <CommandInput
            placeholder={t("variablePickerSearch")}
            className="h-8 text-xs"
          />
          <CommandList>
            <CommandEmpty className="py-4 text-xs">
              {t("variablePickerEmpty")}
            </CommandEmpty>
            {hasVariables &&
              groupedVariables.map((group) => (
                <CommandGroup
                  key={group.nodeId}
                  heading={
                    <span className="flex items-center gap-1.5">
                      <span className={cn("shrink-0", nodeTypeColors[group.nodeType])}>
                        {nodeTypeIcons[group.nodeType]}
                      </span>
                      <span className="truncate text-[10px]">
                        {t(`nodeType_${group.nodeType}` as Parameters<typeof t>[0])}
                      </span>
                      <span className="text-[10px] text-muted-foreground/50 font-mono truncate">
                        {group.nodeId}
                      </span>
                    </span>
                  }
                >
                  {group.variables.map((v) => (
                    <CommandItem
                      key={v.reference}
                      value={`${v.nodeId} ${v.variableName} ${v.reference}`}
                      onSelect={() => handleSelect(v.reference)}
                      className="text-xs cursor-pointer"
                    >
                      <code className="font-mono text-[11px] text-foreground/80">
                        {v.reference}
                      </code>
                    </CommandItem>
                  ))}
                </CommandGroup>
              ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
