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
import { useState, useEffect, forwardRef, useImperativeHandle } from "react"
import { useTranslations } from "next-intl"
import {
  Loader2,
  Wrench,
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
} from "lucide-react"
import { useAuth } from "@/contexts/auth-context"
import { UserAvatar } from "@/components/shared/user-avatar"
import type {
  DagPhaseEvent,
  DagDoneEvent,
} from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import { IterationCard, ArtifactChips } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import { CollapsibleText } from "@/components/playground/collapsible-text"
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
  hideDagGraph?: boolean
  hideStepCards?: boolean
  injectEvents?: Array<{ content: string; phase?: string; timestamp: number }>
  onSuggestionSelect?: (query: string) => void
}

export const DagOutput = forwardRef<DagOutputHandle, DagOutputProps>(function DagOutput({
  planSteps,
  stepStates,
  analysisPhase,
  doneEvent,
  currentPhase,
  currentRound = 1,
  hideDagGraph,
  hideStepCards,
  injectEvents = [],
  onSuggestionSelect,
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
        {/* Collapsible step group */}
        <div className="rounded-lg border border-border/40 bg-muted/20">
          <button
            type="button"
            onClick={() => setStepsExpanded((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground rounded-lg"
          >
            <Wrench className="h-3.5 w-3.5 shrink-0" />
            <span className="font-mono tabular-nums">{summaryParts.join(" \u00b7 ")}</span>
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
              {!hideStepCards && stepStates.map((state) => (
                <div key={state.step_id} data-step-id={state.step_id}>
                  <StepProgressCard state={state} />
                </div>
              ))}
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
        <DagDoneCard done={doneEvent} stepStates={stepStates} onSuggestionSelect={onSuggestionSelect} />
      </div>
    )
  }

  // Streaming / in-progress: render everything expanded as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Planning spinner */}
      {currentPhase === "planning" && !planSteps && (
        <Card className="border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm shiny-text">
              {currentRound > 1
                ? t("replanningRound", { round: currentRound })
                : t("planningSteps")}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Re-planning spinner (between analyze and next planning:start) */}
      {currentPhase === "replanning" && (
        <Card className="border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm shiny-text">
              {t("replanning")}
            </span>
          </CardContent>
        </Card>
      )}

      {/* DAG flow graph */}
      {!hideDagGraph && planSteps && planSteps.length > 0 && (
        <DagFlowGraph planSteps={planSteps} stepStates={stepStates} />
      )}

      {/* Step progress cards */}
      {stepStates.length > 0 &&
        currentPhase !== "planning" &&
        stepStates.map((state) => (
          <div key={state.step_id} data-step-id={state.step_id}>
            <StepProgressCard state={state} />
          </div>
        ))}

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
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-purple-500" />
            <span className="text-sm shiny-text">{t("analyzingResults")}</span>
          </CardContent>
        </Card>
      )}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} stepStates={stepStates} onSuggestionSelect={onSuggestionSelect} />}
    </div>
  )
})

/* ------------------------------------------------------------------ */
/*  Restored from commit 44ca9e1 — battle-tested components            */
/* ------------------------------------------------------------------ */

function StepProgressCard({ state }: { state: StepState }) {
  const StatusIcon =
    state.status === "skipped"
      ? SkipForward
      : state.status === "completed"
        ? CheckCircle2
        : state.status === "running"
          ? Loader2
          : CircleDashed

  const cardBorderClass =
    state.status === "skipped"
      ? "border-zinc-500/20 opacity-50"
      : state.status === "completed"
        ? "border-green-500/20"
        : state.status === "running"
          ? "border-amber-500/20"
          : "border-zinc-500/20"

  const iconBgClass =
    state.status === "skipped"
      ? "bg-zinc-500/10"
      : state.status === "completed"
        ? "bg-green-500/10"
        : state.status === "running"
          ? "bg-amber-500/10"
          : "bg-zinc-500/10"

  const iconTextClass =
    state.status === "skipped"
      ? "text-zinc-500"
      : state.status === "completed"
        ? "text-green-500"
      : state.status === "running"
        ? "text-amber-500"
        : "text-zinc-500"

  const badgeBorderClass =
    state.status === "skipped"
      ? "border-zinc-500/30 text-zinc-500 line-through"
      : state.status === "completed"
        ? "border-green-500/30 text-green-500"
        : state.status === "running"
          ? "border-amber-500/30 text-amber-500"
          : "border-zinc-500/30 text-zinc-500"

  return (
    <Card
      className={`py-4 gap-3 ${cardBorderClass}`}
    >
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2 min-w-0">
          <div
            className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${iconBgClass}`}
          >
            <StatusIcon
              className={`h-3.5 w-3.5 ${iconTextClass}${state.status === "running" ? " animate-spin" : ""}`}
            />
          </div>
          <Badge
            variant="outline"
            className={`${badgeBorderClass} text-[10px] font-mono shrink-0`}
          >
            {state.step_id}
          </Badge>
          <span className="text-sm font-medium text-foreground truncate min-w-0">
            {state.task}
          </span>
          {state.status === "completed" && state.duration != null && (
            <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 font-mono tabular-nums">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(state.duration)}
            </span>
          )}
          {state.status === "running" && state.started_at != null && (
            <ElapsedTimer startedAt={state.started_at} />
          )}
        </div>
      </CardHeader>

      {(state.iterations.length > 0 || state.result) && (
        <CardContent className="space-y-2">
          {/* Iteration items */}
          {state.iterations.map((iter, idx) => {
            const iterData: IterationData = {
              type: iter.type,
              iteration: iter.iteration,
              displayIteration: idx + 1,
              tool_name: iter.tool_name,
              tool_args: iter.tool_args,
              reasoning: iter.reasoning,
              observation: iter.observation,
              error: iter.error,
              loading: iter.loading,
              duration: iter.duration,
              content_type: iter.content_type,
              artifacts: iter.artifacts,
            }
            return (
              <IterationCard
                key={idx}
                data={iterData}
                variant="inline"
                size="compact"
                defaultCollapsed={true}
              />
            )
          })}

          {/* Completed result */}
          {state.result && (
            <ResultBlock content={state.result} />
          )}
        </CardContent>
      )}
    </Card>
  )
}

function ElapsedTimer({ startedAt }: { startedAt: number }) {
  const [elapsed, setElapsed] = useState(() => Date.now() / 1000 - startedAt)
  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() / 1000 - startedAt), 100)
    return () => clearInterval(id)
  }, [startedAt])
  return (
    <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0 font-mono tabular-nums">
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

function DagDoneCard({ done, stepStates, onSuggestionSelect }: { done: DagDoneEvent; stepStates?: StepState[]; onSuggestionSelect?: (query: string) => void }) {
  const t = useTranslations("playground")
  const tDag = useTranslations("dag")

  // Collect all artifacts from all steps
  const allArtifacts = (stepStates ?? []).flatMap(state =>
    state.iterations.flatMap(iter => iter.artifacts ?? [])
  )

  return (
    <Card className="border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">{t("result")}</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1 font-mono tabular-nums">
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
              <span className="flex items-center gap-1 font-mono tabular-nums">
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
        {allArtifacts.length > 0 && (
          <div className="mt-3 pt-3 border-t border-border/30">
            <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">
              {tDag("generatedFiles")}
            </p>
            <ArtifactChips artifacts={allArtifacts} />
          </div>
        )}
        {done.suggestions?.length && onSuggestionSelect ? (
          <SuggestedFollowups
            suggestions={done.suggestions}
            onSelect={onSuggestionSelect}
          />
        ) : null}
      </CardContent>
    </Card>
  )
}
