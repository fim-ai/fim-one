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
import { useState } from "react"
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
  User,
} from "lucide-react"
import type {
  DagPhaseEvent,
  DagDoneEvent,
} from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"
import { IterationCard } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import { SuggestedFollowups } from "./suggested-followups"

interface DagOutputProps {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  doneEvent: DagDoneEvent | null
  currentPhase: string | null
  currentRound?: number
  hideDagGraph?: boolean
  injectEvents?: Array<{ content: string; phase?: string; timestamp: number }>
  onSuggestionSelect?: (query: string) => void
}

export function DagOutput({
  planSteps,
  stepStates,
  analysisPhase,
  doneEvent,
  currentPhase,
  currentRound = 1,
  hideDagGraph,
  injectEvents = [],
  onSuggestionSelect,
}: DagOutputProps) {
  const [stepsExpanded, setStepsExpanded] = useState(false)

  const completedSteps = stepStates.filter(
    (s) => s.status === "completed",
  ).length
  const totalSteps = stepStates.length

  // After completion: collapsible summary bar + always-visible done card
  if (doneEvent && totalSteps > 0) {
    const summaryParts: string[] = [
      `${completedSteps}/${totalSteps} step${totalSteps !== 1 ? "s" : ""} completed`,
      fmtDuration(doneEvent.elapsed),
    ]
    if (doneEvent.rounds != null && doneEvent.rounds > 1) {
      summaryParts.push(`${doneEvent.rounds} rounds`)
    }

    return (
      <div className="space-y-3 min-w-0 w-full">
        {/* Collapsible summary bar */}
        <button
          type="button"
          onClick={() => setStepsExpanded((v) => !v)}
          className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 flex w-full items-center gap-2 px-4 py-2.5 rounded-lg border border-border/40 bg-muted/20 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground"
        >
          <Wrench className="h-3.5 w-3.5 shrink-0" />
          <span>{summaryParts.join(" \u00b7 ")}</span>
          {stepsExpanded ? (
            <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
          )}
        </button>

        {/* Inject messages — always visible */}
        {injectEvents.map((evt, i) => (
          <div key={`inject-${i}`} className="flex gap-3 animate-in fade-in-0 slide-in-from-bottom-2 duration-300">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
              <User className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="flex-1 pt-0.5">
              <p className="text-sm text-foreground">{evt.content}</p>
            </div>
          </div>
        ))}

        {/* Expanded: DAG graph + step cards + analysis */}
        {stepsExpanded && (
          <div className="space-y-3 animate-in fade-in-0 slide-in-from-top-1 duration-200">
            {!hideDagGraph && planSteps && planSteps.length > 0 && (
              <DagFlowGraph planSteps={planSteps} stepStates={stepStates} />
            )}
            {stepStates.map((state) => (
              <div key={state.step_id} data-step-id={state.step_id}>
                <StepProgressCard state={state} />
              </div>
            ))}
            {analysisPhase && <AnalysisCard phase={analysisPhase} />}
          </div>
        )}

        {/* Done card — always visible */}
        <DagDoneCard done={doneEvent} onSuggestionSelect={onSuggestionSelect} />
      </div>
    )
  }

  // Streaming / in-progress: render everything expanded as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Planning spinner */}
      {currentPhase === "planning" && !planSteps && (
        <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm shiny-text">
              {currentRound > 1
                ? `Re-planning execution steps (Round ${currentRound})...`
                : "Planning execution steps..."}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Re-planning spinner (between analyze and next planning:start) */}
      {currentPhase === "replanning" && (
        <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm shiny-text">
              Re-planning...
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
        <div key={`inject-${i}`} className="flex gap-3 animate-in fade-in-0 slide-in-from-bottom-2 duration-300">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
            <User className="h-3.5 w-3.5 text-primary" />
          </div>
          <div className="flex-1 pt-0.5">
            <p className="text-sm text-foreground">{evt.content}</p>
          </div>
        </div>
      ))}

      {/* Analysis phase */}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} onSuggestionSelect={onSuggestionSelect} />}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Restored from commit 44ca9e1 — battle-tested components            */
/* ------------------------------------------------------------------ */

function StepProgressCard({ state }: { state: StepState }) {
  const StatusIcon =
    state.status === "completed"
      ? CheckCircle2
      : state.status === "running"
        ? Loader2
        : CircleDashed

  const cardBorderClass =
    state.status === "completed"
      ? "border-green-500/20"
      : state.status === "running"
        ? "border-amber-500/20"
        : "border-zinc-500/20"

  const iconBgClass =
    state.status === "completed"
      ? "bg-green-500/10"
      : state.status === "running"
        ? "bg-amber-500/10"
        : "bg-zinc-500/10"

  const iconTextClass =
    state.status === "completed"
      ? "text-green-500"
      : state.status === "running"
        ? "text-amber-500"
        : "text-zinc-500"

  const badgeBorderClass =
    state.status === "completed"
      ? "border-green-500/30 text-green-500"
      : state.status === "running"
        ? "border-amber-500/30 text-amber-500"
        : "border-zinc-500/30 text-zinc-500"

  return (
    <Card
      className={`py-4 ${cardBorderClass}`}
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
            <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground shrink-0">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(state.duration)}
            </span>
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

/* ------------------------------------------------------------------ */
/*  Shared components                                                  */
/* ------------------------------------------------------------------ */

function ResultBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false)

  // First non-empty line as preview (strip markdown)
  const preview = content.split("\n").find((l) => l.trim())?.replace(/^#+\s*/, "").trim() ?? ""
  const shortPreview = preview.length > 40 ? preview.slice(0, 40) + "…" : preview

  return (
    <div className="rounded-md border border-border/30 bg-muted/20 p-2.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-2 text-left cursor-pointer group"
      >
        <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
        <span className="font-medium text-foreground text-xs">Result</span>
        {!expanded && shortPreview && (
          <span className="text-[10px] text-muted-foreground truncate min-w-0">{shortPreview}</span>
        )}
        <ChevronDown className={`h-3 w-3 text-muted-foreground shrink-0 ml-auto transition-transform ${expanded ? "rotate-180" : ""}`} />
      </button>
      {expanded && (
        <div className="mt-2">
          <MarkdownContent
            content={content}
            className="text-xs text-foreground/80 [&_h1]:text-sm [&_h2]:text-xs [&_h3]:text-xs [&_h4]:text-xs [&_p]:text-xs [&_li]:text-xs [&_td]:text-xs [&_th]:text-xs [&_table]:text-xs"
          />
        </div>
      )}
    </div>
  )
}

function AnalysisCard({ phase }: { phase: DagPhaseEvent }) {
  return (
    <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-purple-500/20 py-4">
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
              Analysis
            </Badge>
            {phase.achieved != null && (
              <span className={`flex items-center gap-1 text-[10px] ${phase.achieved ? "text-green-500" : "text-destructive"}`}>
                <Target className="h-2.5 w-2.5" />
                {phase.achieved ? "Goal Achieved" : "Goal Not Achieved"}
              </span>
            )}
            {phase.confidence != null && (
              <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <Gauge className="h-2.5 w-2.5" />
                {(phase.confidence * 100).toFixed(0)}% confidence
              </span>
            )}
          </div>
          {phase.reasoning && (
            <p className="text-sm text-muted-foreground leading-relaxed">
              {phase.reasoning}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function DagDoneCard({ done, onSuggestionSelect }: { done: DagDoneEvent; onSuggestionSelect?: (query: string) => void }) {
  return (
    <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">Result</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(done.elapsed)}
            </span>
            {done.rounds != null && done.rounds > 1 && (
              <span className="flex items-center gap-1">
                <RefreshCw className="h-2.5 w-2.5" />
                {done.rounds} rounds
              </span>
            )}
            {done.usage && (
              <span className="flex items-center gap-1">
                <BarChart3 className="h-2.5 w-2.5" />
                {(done.usage.prompt_tokens / 1000).toFixed(1)}k in · {(done.usage.completion_tokens / 1000).toFixed(1)}k out
              </span>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <MarkdownContent
          content={done.answer}
          className="prose-sm text-sm text-foreground/90"
        />
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
