"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import { fmtDuration } from "@/lib/utils"
import { useState, useEffect, useMemo, forwardRef, useImperativeHandle } from "react"
import { useTranslations } from "next-intl"
import type { LucideIcon } from "lucide-react"
import {
  Loader2,
  Wrench,
  Brain,
  CheckCircle2,
  CircleDashed,
  BarChart3,
  Clock,
  Target,
  Gauge,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  ChevronRight,
  SkipForward,
  AlertCircle,
  StopCircle,
} from "lucide-react"
import { useAuth } from "@/contexts/auth-context"
import { UserAvatar } from "@/components/shared/user-avatar"
import type {
  DagPhaseEvent,
  DagDoneEvent,
} from "@/types/api"
import type { StepState, RoundSnapshot } from "@/hooks/use-dag-steps"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import { IterationCard, ArtifactChips } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible"
import { CollapsibleText } from "@/components/playground/collapsible-text"
import { getToolDisplayName, getToolIcon } from "@/components/steps/step-summary"
import { useToolCatalog } from "@/hooks/use-tool-catalog"
import type { ToolMeta } from "@/hooks/use-tool-catalog"
import { SuggestedFollowups } from "./suggested-followups"
import { stripCitations } from "@/lib/evidence-utils"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { ScrollArea } from "@/components/ui/scroll-area"

export interface DagOutputHandle {
  expandSteps: () => void
}

interface DagOutputProps {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  doneEvent: DagDoneEvent | null
  currentPhase: string | null
  currentRound?: number
  previousRounds?: RoundSnapshot[]
  hideDagGraph?: boolean
  hideStepCards?: boolean
  injectEvents?: Array<{ content: string; phase?: string; timestamp: number }>
  streamingAnswer?: string
  answerDone?: boolean
  suggestions?: string[]
  onSuggestionSelect?: (query: string) => void
  isPostProcessing?: boolean
}

export const DagOutput = forwardRef<DagOutputHandle, DagOutputProps>(function DagOutput({
  planSteps,
  stepStates,
  analysisPhase,
  doneEvent,
  currentPhase,
  currentRound = 1,
  previousRounds = [],
  hideDagGraph,
  hideStepCards,
  injectEvents = [],
  streamingAnswer,
  answerDone,
  suggestions,
  onSuggestionSelect,
  isPostProcessing,
}, ref) {
  const t = useTranslations("playground")
  const { user } = useAuth()
  const userFallback = (user?.display_name || user?.email || "U").charAt(0).toUpperCase()
  const [stepsExpanded, setStepsExpanded] = useState(false)

  useImperativeHandle(ref, () => ({
    expandSteps: () => setStepsExpanded(true),
  }), [])

  const completedSteps = stepStates.filter(
    (s) => s.status === "completed",
  ).length
  const totalSteps = stepStates.length

  // Determine which answer to show: doneEvent.answer is authoritative, fall back to streaming
  const displayAnswer = doneEvent?.answer ?? streamingAnswer ?? ""
  const isAnswerStreaming = !!streamingAnswer && !doneEvent && !answerDone

  // After completion: collapsible summary bar + always-visible done card
  if (doneEvent && totalSteps > 0) {
    const summaryParts: string[] = [
      totalSteps !== 1
        ? t("stepsCompletedPlural", { completed: completedSteps, total: totalSteps })
        : t("stepsCompleted", { completed: completedSteps, total: totalSteps }),
      fmtDuration(doneEvent.elapsed),
    ]
    if (doneEvent.rounds != null && doneEvent.rounds > 1) {
      summaryParts.push(t("roundCount", { count: doneEvent.rounds }))
    }

    return (
      <div className="space-y-3 min-w-0 w-full">
        {/* Previous rounds (collapsed, showing failure) */}
        {previousRounds.map((snapshot) => (
          <PreviousRoundCard key={snapshot.round} snapshot={snapshot} hideDagGraph={hideDagGraph} hideStepCards={hideStepCards} />
        ))}

        {/* Collapsible step group */}
        <div className="rounded-lg border border-border/40 bg-muted/20">
          <button
            type="button"
            onClick={() => setStepsExpanded((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground rounded-lg"
          >
            <Wrench className="h-3.5 w-3.5 shrink-0" />
            <span className="tabular-nums">{summaryParts.join(" \u00b7 ")}</span>
            {stepsExpanded ? (
              <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
            )}
          </button>

          {/* Expanded: DAG graph + step cards + analysis — nested inside */}
          {stepsExpanded && (
            <div className="space-y-3 px-4 pb-3">
              {!hideDagGraph && planSteps && planSteps.length > 0 && (
                <DagFlowGraph planSteps={planSteps} stepStates={stepStates} />
              )}
              {!hideStepCards && stepStates.length > 0 && (
                <StepList stepStates={stepStates} />
              )}
              {!hideStepCards && analysisPhase && <AnalysisCard phase={analysisPhase} />}
            </div>
          )}
        </div>

        {/* Inject messages — always visible */}
        {injectEvents.map((evt, i) => (
          <div key={`inject-${i}`} className="flex gap-3">
            <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7" iconClassName="h-3.5 w-3.5" />
            <div className="flex-1 pt-0.5">
              <CollapsibleText content={evt.content} className="text-sm text-foreground whitespace-pre-wrap" />
            </div>
          </div>
        ))}

        {/* Done card — always visible */}
        <DagDoneCard done={doneEvent} stepStates={stepStates} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} isPostProcessing={isPostProcessing} />
      </div>
    )
  }

  // Streaming / in-progress: render everything expanded as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Previous rounds (collapsed, showing failure) */}
      {previousRounds.map((snapshot) => (
        <PreviousRoundCard key={snapshot.round} snapshot={snapshot} hideDagGraph={hideDagGraph} hideStepCards={hideStepCards} />
      ))}

      {/* Planning spinner */}
      {currentPhase === "planning" && !planSteps && (
        <Card className="border-border py-4">
          <CardContent className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <span className="text-sm text-shimmer">
                {currentRound > 1
                  ? t("replanningRound", { round: currentRound })
                  : t("planningSteps")}
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Re-planning spinner (between analyze and next planning:start) */}
      {currentPhase === "replanning" && (
        <Card className="border-border py-4">
          <CardContent className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              <span className="text-sm text-shimmer">
                {t("replanning")}
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* DAG flow graph */}
      {!hideDagGraph && planSteps && planSteps.length > 0 && (
        <DagFlowGraph planSteps={planSteps} stepStates={stepStates} />
      )}

      {/* Step progress cards */}
      {stepStates.length > 0 && currentPhase !== "planning" && (
        <StepList stepStates={stepStates} />
      )}

      {/* Inject messages */}
      {injectEvents.map((evt, i) => (
        <div key={`inject-${i}`} className="flex gap-3">
          <UserAvatar avatar={user?.avatar} userId={user?.id} fallback={userFallback} className="h-7 w-7" iconClassName="h-3.5 w-3.5" />
          <div className="flex-1 pt-0.5">
            <CollapsibleText content={evt.content} className="text-sm text-foreground whitespace-pre-wrap" />
          </div>
        </div>
      ))}

      {/* Analysis phase — spinner while waiting, then full card */}
      {currentPhase === "analyzing" && !analysisPhase && (
        <Card className="border-purple-500/20 py-4">
          <CardContent className="flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Loader2 className="h-4 w-4 animate-spin text-purple-500" />
              <span className="text-sm text-shimmer">{t("analyzingResults")}</span>
            </div>
          </CardContent>
        </Card>
      )}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} stepStates={stepStates} suggestions={suggestions} onSuggestionSelect={onSuggestionSelect} isPostProcessing={isPostProcessing} />}

      {/* Streaming answer — shown before done arrives */}
      {isAnswerStreaming && displayAnswer && (
        <DagStreamingAnswerCard content={displayAnswer} />
      )}

      {/* Aborted partial answer — show without spinner */}
      {!isAnswerStreaming && answerDone && !doneEvent && displayAnswer && (
        <DagStreamingAnswerCard content={displayAnswer} aborted />
      )}
    </div>
  )
})

function PreviousRoundCard({ snapshot, hideDagGraph, hideStepCards }: { snapshot: RoundSnapshot; hideDagGraph?: boolean; hideStepCards?: boolean }) {
  const t = useTranslations("playground")
  const [expanded, setExpanded] = useState(false)
  const completedSteps = snapshot.stepStates.filter(s => s.status === "completed").length
  const totalSteps = snapshot.stepStates.length

  return (
    <div className="rounded-lg border border-destructive/20 bg-destructive/5">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-destructive/10 transition-colors text-xs text-muted-foreground rounded-lg"
      >
        <RefreshCw className="h-3.5 w-3.5 shrink-0 text-destructive/60" />
        <span className="font-medium text-destructive/80">
          {t("previousRoundHeader", { round: snapshot.round })}
        </span>
        <span className="tabular-nums">
          {t("previousRoundSummary", { completed: completedSteps, total: totalSteps })}
        </span>
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
        )}
      </button>

      {expanded && (
        <div className="space-y-3 px-4 pb-3">
          {!hideDagGraph && snapshot.planSteps && snapshot.planSteps.length > 0 && (
            <DagFlowGraph planSteps={snapshot.planSteps} stepStates={snapshot.stepStates} />
          )}
          {!hideStepCards && snapshot.stepStates.length > 0 && (
            <StepList stepStates={snapshot.stepStates} />
          )}
          {!hideStepCards && snapshot.analysisPhase && <AnalysisCard phase={snapshot.analysisPhase} />}
        </div>
      )}
    </div>
  )
}

function DagStreamingAnswerCard({ content, aborted }: { content: string; aborted?: boolean }) {
  const t = useTranslations("playground")
  return (
    <Card className="border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            {aborted ? (
              <StopCircle className="h-3.5 w-3.5 text-green-500" />
            ) : (
              <Loader2 className="h-3.5 w-3.5 text-green-500 animate-spin" />
            )}
          </div>
          <CardTitle className="text-sm">{t("result")}</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <MarkdownContent
          content={stripCitations(content)}
          className={`prose-sm text-sm text-foreground/90${aborted ? "" : " streaming-cursor"}`}
        />
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------ */
/*  Restored from commit 44ca9e1 — battle-tested components            */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/*  Iteration grouping helpers                                         */
/* ------------------------------------------------------------------ */

interface IterationGroupData {
  toolName: string
  displayName: string
  Icon: LucideIcon
  items: Array<{ data: IterationData; index: number }>
  totalDuration: number
}

/** Group consecutive iterations by tool_name for compact expanded display */
function groupConsecutiveIterations(
  iterations: StepState["iterations"],
  tools?: ToolMeta[],
): IterationGroupData[] {
  const groups: IterationGroupData[] = []
  for (let i = 0; i < iterations.length; i++) {
    const iter = iterations[i]
    const name = iter.tool_name || "__thinking__"
    const last = groups[groups.length - 1]

    const iterData: IterationData = {
      type: iter.type,
      iteration: iter.iteration,
      displayIteration: i + 1,
      tool_name: iter.tool_name,
      tool_args: iter.tool_args,
      reasoning: iter.reasoning,
      observation: iter.observation,
      error: iter.error,
      loading: iter.loading,
      duration: iter.duration,
      content_type: iter.content_type,
      artifacts: iter.artifacts,
      thinkingText: iter.thinkingText,
    }

    if (last && last.toolName === name) {
      last.items.push({ data: iterData, index: i })
      last.totalDuration += iter.duration ?? 0
    } else {
      groups.push({
        toolName: name,
        displayName: name === "__thinking__" ? "Thinking" : getToolDisplayName(name, tools),
        Icon: name === "__thinking__" ? Brain : getToolIcon(name, tools),
        items: [{ data: iterData, index: i }],
        totalDuration: iter.duration ?? 0,
      })
    }
  }
  return groups
}

/** Count iterations by tool type for collapsed step summary (excludes thinking) */
function countIterationsByTool(
  iterations: StepState["iterations"],
  tools?: ToolMeta[],
): Array<{ toolName: string; displayName: string; Icon: LucideIcon; count: number }> {
  const counts = new Map<string, { displayName: string; Icon: LucideIcon; count: number }>()
  for (const iter of iterations) {
    const name = iter.tool_name || "__thinking__"
    if (name === "__thinking__") continue
    const existing = counts.get(name)
    if (existing) {
      existing.count++
    } else {
      counts.set(name, {
        displayName: getToolDisplayName(name, tools),
        Icon: getToolIcon(name, tools),
        count: 1,
      })
    }
  }
  return Array.from(counts.entries()).map(([toolName, data]) => ({
    toolName,
    ...data,
  }))
}

/* ------------------------------------------------------------------ */
/*  StepList — timeline wrapper                                        */
/* ------------------------------------------------------------------ */

function StepList({ stepStates }: { stepStates: StepState[] }) {
  return (
    <div className="relative">
      {stepStates.map((state, idx) => (
        <div key={state.step_id} className="relative" data-step-id={state.step_id}>
          {/* Timeline connector line to next step */}
          {idx < stepStates.length - 1 && (
            <div className="absolute left-[10px] top-[22px] bottom-0 w-px bg-border/30" />
          )}
          <StepProgressCard state={state} />
        </div>
      ))}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  CollapsedIterationGroup — grouped same-tool iterations             */
/* ------------------------------------------------------------------ */

function CollapsedIterationGroup({ group }: { group: IterationGroupData }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div>
      <div
        className="rounded-md border border-border/30 bg-muted/20 px-2.5 py-2 cursor-pointer group hover:bg-muted/30 transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="flex items-center gap-2">
          <group.Icon className="h-3 w-3 text-amber-500 shrink-0" />
          <span className="text-xs font-medium text-foreground">
            {group.displayName}
          </span>
          <span className="text-[10px] text-muted-foreground font-medium">
            ×{group.items.length}
          </span>
          <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
            <Clock className="h-2.5 w-2.5" />
            {fmtDuration(group.totalDuration)}
          </span>
          {expanded
            ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          }
        </div>
      </div>
      {expanded && (
        <div className="ml-4 mt-1.5 space-y-1.5">
          {group.items.map(({ data, index }) => (
            <IterationCard
              key={index}
              data={data}
              variant="inline"
              size="compact"
              defaultCollapsed={true}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  StepProgressCard — collapsible timeline node                       */
/* ------------------------------------------------------------------ */

function StepProgressCard({ state }: { state: StepState }) {
  const tDag = useTranslations("dag")
  const { data: catalog } = useToolCatalog()
  const [expanded, setExpanded] = useState(state.status === "running")

  // Auto-expand when step transitions to running
  useEffect(() => {
    if (state.status === "running") setExpanded(true)
  }, [state.status])

  const StatusIcon =
    state.status === "failed"
      ? AlertCircle
      : state.status === "skipped"
        ? SkipForward
        : state.status === "completed"
          ? CheckCircle2
          : state.status === "running"
            ? Loader2
            : CircleDashed

  const iconBgClass =
    state.status === "failed"
      ? "bg-red-500/10"
      : state.status === "skipped"
        ? "bg-zinc-500/10"
        : state.status === "completed"
          ? "bg-green-500/10"
          : state.status === "running"
            ? "bg-amber-500/10"
            : "bg-zinc-500/10"

  const iconTextClass =
    state.status === "failed"
      ? "text-red-500"
      : state.status === "skipped"
        ? "text-zinc-500"
        : state.status === "completed"
          ? "text-green-500"
          : state.status === "running"
            ? "text-amber-500"
            : "text-zinc-500"

  const badgeBorderClass =
    state.status === "failed"
      ? "border-red-500/30 text-red-500"
      : state.status === "skipped"
        ? "border-zinc-500/30 text-zinc-500 line-through"
        : state.status === "completed"
          ? "border-green-500/30 text-green-500"
          : state.status === "running"
            ? "border-amber-500/30 text-amber-500"
            : "border-zinc-500/30 text-zinc-500"

  // Tool summary for collapsed state
  const toolSummary = useMemo(() =>
    countIterationsByTool(state.iterations, catalog?.tools),
    [state.iterations, catalog?.tools]
  )

  // Grouped iterations for expanded state
  const iterGroups = useMemo(() =>
    groupConsecutiveIterations(state.iterations, catalog?.tools),
    [state.iterations, catalog?.tools]
  )

  const hasContent = state.iterations.length > 0 || !!state.result

  return (
    <div className={`pl-8 pb-3 relative${state.status === "skipped" ? " opacity-50" : ""}`}>
      {/* Timeline dot */}
      <div className={`absolute left-0 top-0 z-10 flex h-[22px] w-[22px] items-center justify-center rounded-full ${iconBgClass} ring-2 ring-background`}>
        <StatusIcon
          className={`h-3 w-3 ${iconTextClass}${state.status === "running" ? " animate-spin" : ""}`}
        />
      </div>

      {/* Clickable header */}
      <div
        className={`rounded-md px-2 py-1 transition-colors ${hasContent ? "cursor-pointer hover:bg-muted/30" : ""}`}
        onClick={hasContent ? () => setExpanded(v => !v) : undefined}
      >
        <div className="space-y-1">
          <div className="flex items-center gap-2 min-w-0">
            <Badge
              variant="outline"
              className={`${badgeBorderClass} text-[10px] shrink-0`}
            >
              {state.step_id}
            </Badge>
            {state.status === "completed" && state.duration != null && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
                <Clock className="h-2.5 w-2.5" />
                {fmtDuration(state.duration)}
              </span>
            )}
            {state.status === "running" && state.started_at != null && (
              <ElapsedTimer startedAt={state.started_at} />
            )}
            {hasContent && (
              expanded
                ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            )}
          </div>
          <p className="text-sm font-medium text-foreground">
            {state.task}
          </p>
          {state.verifying && (
            <div className="flex items-center gap-1.5 mt-0.5">
              <Loader2 className="h-2.5 w-2.5 animate-spin text-blue-500" />
              <span className="text-[10px] text-blue-500">
                {state.verifying === "citations"
                  ? tDag("verifyingCitations")
                  : tDag("verifyingStep")}
              </span>
            </div>
          )}
        </div>

        {/* Collapsed: tool summary badges */}
        {!expanded && toolSummary.length > 0 && (
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {toolSummary.map(({ toolName, displayName, Icon, count }) => (
              <span
                key={toolName}
                className="inline-flex items-center gap-1 text-[10px] text-muted-foreground bg-muted/40 rounded px-1.5 py-0.5"
              >
                <Icon className="h-2.5 w-2.5" />
                <span>{displayName}</span>
                {count > 1 && <span className="font-medium">×{count}</span>}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Expanded: interleaved thinking + tool iterations + result */}
      {expanded && hasContent && (
        <div className="mt-1.5 space-y-1.5 pl-2">
          {iterGroups.map((group, gIdx) =>
            group.toolName === "__thinking__" ? (
              group.items.map(({ data, index }) => {
                const isStreaming = !!data.loading
                return (
                  <div key={index} className="rounded-md border border-border/30 bg-muted/10 px-2.5 py-2">
                    <div className="flex items-center gap-1.5 mb-1">
                      <Brain className="h-3 w-3 text-muted-foreground" />
                      <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                        {tDag("thinking")}
                      </span>
                    </div>
                    <div className="text-xs italic text-muted-foreground leading-relaxed">
                      <MarkdownContent
                        content={data.thinkingText ?? ""}
                        className={`prose-xs text-xs text-muted-foreground${isStreaming ? " streaming-cursor" : ""}`}
                      />
                    </div>
                  </div>
                )
              })
            ) : group.items.length >= 3 ? (
              <CollapsedIterationGroup key={gIdx} group={group} />
            ) : (
              group.items.map(({ data, index }) => (
                <IterationCard
                  key={index}
                  data={data}
                  variant="inline"
                  size="compact"
                  defaultCollapsed={true}
                />
              ))
            )
          )}
          {state.result && <ResultBlock content={state.result} />}
        </div>
      )}
    </div>
  )
}

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() => Date.now() / 1000 - startedAt)
  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() / 1000 - startedAt), 100)
    return () => clearInterval(id)
  }, [startedAt])
  return (
    <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 tabular-nums">
      <Clock className="h-2.5 w-2.5" />
      {fmtDuration(elapsed)}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/*  Shared components                                                  */
/* ------------------------------------------------------------------ */

function stripInlineMarkdown(s: string): string {
  return s
    .replace(/^#+\s*/, "")              // headings
    .replace(/\*\*(.*?)\*\*/g, "$1")    // bold
    .replace(/\*(.*?)\*/g, "$1")        // italic
    .replace(/~~(.*?)~~/g, "$1")        // strikethrough
    .replace(/`(.*?)`/g, "$1")          // inline code
    .replace(/\[(.*?)\]\(.*?\)/g, "$1") // links
    .trim()
}

export function ResultBlock({ content }: { content: string }) {
  const t = useTranslations("playground")
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Strip orphan citation markers [1], [10] etc. — DAG mode has no References panel
  const cleanContent = stripCitations(content)

  // First non-empty line as preview (strip all markdown syntax)
  const preview = stripInlineMarkdown(cleanContent.split("\n").find((l) => l.trim()) ?? "")
  const shortPreview = preview.length > 40 ? preview.slice(0, 40) + "…" : preview

  return (
    <>
      <div
        className="rounded-md border border-border/30 bg-muted/20 px-2.5 py-2 cursor-pointer group hover:bg-muted/30 transition-colors"
        onClick={() => setDrawerOpen(true)}
      >
        <div className="flex items-center gap-2">
          <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
          <span className="font-medium text-foreground text-xs">{t("result")}</span>
          {shortPreview && (
            <span className="text-[10px] text-muted-foreground truncate min-w-0">{shortPreview}</span>
          )}
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground shrink-0 ml-auto group-hover:text-foreground transition-colors" />
        </div>
      </div>
      <ResultDetailDrawer
        content={drawerOpen ? cleanContent : null}
        onClose={() => setDrawerOpen(false)}
      />
    </>
  )
}

function ResultDetailDrawer({ content, onClose }: { content: string | null; onClose: () => void }) {
  const t = useTranslations("playground")
  return (
    <Sheet open={!!content} onOpenChange={(v) => { if (!v) onClose() }}>
      <SheetContent side="right" className="sm:max-w-2xl w-full flex flex-col p-0 gap-0">
        {content && (
          <>
            <div className="shrink-0 px-6 pt-6 pb-4 border-b border-border/40">
              <SheetHeader className="gap-1">
                <SheetTitle className="flex items-center gap-2.5 text-base">
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-green-500/10">
                    <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
                  </div>
                  <span className="font-semibold">{t("stepResult")}</span>
                </SheetTitle>
              </SheetHeader>
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <div className="px-6 py-4">
                <MarkdownContent
                  content={content}
                  className="prose-sm text-sm text-foreground/90"
                />
              </div>
            </ScrollArea>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}

function AnalysisCard({ phase }: { phase: DagPhaseEvent }) {
  const t = useTranslations("playground")
  return (
    <Card className="border-purple-500/20 py-4">
      <CardContent className="flex items-start gap-3">
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-purple-500/10">
          <BarChart3 className="h-3.5 w-3.5 text-purple-500" />
        </div>
        <div className="space-y-2 min-w-0 flex-1">
          <div className="flex items-center gap-3 flex-wrap">
            <Badge
              variant="outline"
              className="border-purple-500/30 text-purple-500 text-[10px] uppercase tracking-wider"
            >
              {t("analysis")}
            </Badge>
            {phase.achieved != null && (
              <span className={`flex items-center gap-1 text-[10px] ${phase.achieved ? "text-green-500" : "text-destructive"}`}>
                <Target className="h-2.5 w-2.5" />
                {phase.achieved ? t("goalAchieved") : t("goalNotAchieved")}
              </span>
            )}
            {phase.confidence != null && (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Gauge className="h-2.5 w-2.5" />
                {t("confidenceLabel", { value: (phase.confidence * 100).toFixed(0) })}
              </span>
            )}
          </div>
          {phase.reasoning && (
            <MarkdownContent
              content={phase.reasoning}
              className="prose-sm text-sm text-muted-foreground"
            />
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function DagDoneCard({ done, stepStates, suggestions, onSuggestionSelect, isPostProcessing }: { done: DagDoneEvent; stepStates?: StepState[]; suggestions?: string[]; onSuggestionSelect?: (query: string) => void; isPostProcessing?: boolean }) {
  const t = useTranslations("playground")
  const tDag = useTranslations("dag")

  // Collect all artifacts from all steps
  const allArtifacts = (stepStates ?? []).flatMap(state =>
    state.iterations.flatMap(iter => iter.artifacts ?? [])
  )

  // Compute deliverables and other artifacts
  const deliverables = done.deliverables ?? []
  const otherArtifacts = deliverables.length > 0
    ? allArtifacts.filter(a => !deliverables.some(d => d.url === a.url))
    : []

  return (
    <Card className={done.achieved === false ? "border-destructive/20 py-4" : "border-green-500/20 py-4"}>
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${done.achieved === false ? "bg-destructive/10" : "bg-green-500/10"}`}>
            {done.achieved === false
              ? <AlertCircle className="h-3.5 w-3.5 text-destructive" />
              : <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
            }
          </div>
          <CardTitle className="text-sm">{t("result")}</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1 tabular-nums">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(done.elapsed)}
            </span>
            {done.rounds != null && done.rounds > 1 && (
              <span className="flex items-center gap-1">
                <RefreshCw className="h-2.5 w-2.5" />
                {t("roundCount", { count: done.rounds })}
              </span>
            )}
            {done.usage && (
              <span className="flex items-center gap-1 tabular-nums">
                <BarChart3 className="h-2.5 w-2.5" />
                {t("tokenIn", { value: (done.usage.prompt_tokens / 1000).toFixed(1) })} · {t("tokenOut", { value: (done.usage.completion_tokens / 1000).toFixed(1) })}
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <MarkdownContent
          content={stripCitations(done.answer)}
          className="prose-sm text-sm text-foreground/90"
        />
        {deliverables.length > 0 && (
          <div className="mt-3 pt-3 border-t border-border/30">
            <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">
              {tDag("deliverables")}
            </p>
            <ArtifactChips artifacts={deliverables} />
          </div>
        )}
        {otherArtifacts.length > 0 && (
          <div className="mt-2 pt-2 border-t border-border/20">
            <Collapsible defaultOpen={false}>
              <CollapsibleTrigger className="flex items-center gap-1.5 cursor-pointer group">
                <ChevronRight className="h-3 w-3 text-muted-foreground/60 transition-transform duration-200 group-data-[state=open]:rotate-90" />
                <p className="text-[10px] font-medium text-muted-foreground/60 uppercase tracking-wider">
                  {tDag("generatedFilesCount", { count: otherArtifacts.length })}
                </p>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="opacity-60 mt-1.5">
                  <ArtifactChips artifacts={otherArtifacts} />
                </div>
              </CollapsibleContent>
            </Collapsible>
          </div>
        )}
        {deliverables.length === 0 && allArtifacts.length > 0 && (
          <div className="mt-3 pt-3 border-t border-border/30">
            <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">
              {tDag("generatedFiles")}
            </p>
            <ArtifactChips artifacts={allArtifacts} />
          </div>
        )}
        {/* Use prop suggestions first, fall back to done.suggestions for stored conversations */}
        {(isPostProcessing || suggestions?.length || done.suggestions?.length) && onSuggestionSelect ? (
          <SuggestedFollowups
            suggestions={suggestions?.length ? suggestions : done.suggestions!}
            onSelect={onSuggestionSelect}
            isLoading={isPostProcessing}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}
