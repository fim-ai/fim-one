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
  BarChart3,
  Clock,
  Target,
  Gauge,
  RefreshCw,
  ChevronDown,
  ChevronUp,
} from "lucide-react"
import type {
  DagPhaseEvent,
  DagDoneEvent,
} from "@/types/api"
import type { StepState } from "@/hooks/use-dag-steps"
import { DagFlowGraph } from "@/components/dag/dag-flow-graph"

interface DagOutputProps {
  planSteps: DagPhaseEvent["steps"]
  stepStates: StepState[]
  analysisPhase: DagPhaseEvent | null
  doneEvent: DagDoneEvent | null
  currentPhase: string | null
  currentRound?: number
  hideDagGraph?: boolean
}

export function DagOutput({
  planSteps,
  stepStates,
  analysisPhase,
  doneEvent,
  currentPhase,
  currentRound = 1,
  hideDagGraph,
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
          className="flex w-full items-center gap-2 px-4 py-2.5 rounded-lg border border-border/40 bg-muted/20 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground"
        >
          <Wrench className="h-3.5 w-3.5 shrink-0" />
          <span>{summaryParts.join(" \u00b7 ")}</span>
          {stepsExpanded ? (
            <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
          )}
        </button>

        {/* Expanded: DAG graph + step cards + analysis */}
        {stepsExpanded && (
          <div className="space-y-3 animate-in fade-in-0 slide-in-from-top-1 duration-200">
            {!hideDagGraph && planSteps && planSteps.length > 0 && (
              <DagFlowGraph planSteps={planSteps} stepStates={stepStates} />
            )}
            {analysisPhase && <AnalysisCard phase={analysisPhase} />}
          </div>
        )}

        {/* Done card — always visible */}
        <DagDoneCard done={doneEvent} />
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

      {/* Analysis phase */}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} />}
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

function DagDoneCard({ done }: { done: DagDoneEvent }) {
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
      </CardContent>
    </Card>
  )
}
