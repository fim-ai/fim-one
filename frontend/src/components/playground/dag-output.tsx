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
import {
  Loader2,
  Wrench,
  Brain,
  CheckCircle2,
  AlertCircle,
  CircleDashed,
  BarChart3,
  Clock,
  Target,
  Gauge,
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
  hideDagGraph?: boolean
}

export function DagOutput({
  planSteps,
  stepStates,
  analysisPhase,
  doneEvent,
  currentPhase,
  hideDagGraph,
}: DagOutputProps) {
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Planning spinner */}
      {currentPhase === "planning" && !planSteps && (
        <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm shiny-text">
              Planning execution steps...
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

      {/* Analysis phase */}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} />}

    </div>
  )
}

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
        ? "border-blue-500/20"
        : "border-zinc-500/20"

  const iconBgClass =
    state.status === "completed"
      ? "bg-green-500/10"
      : state.status === "running"
        ? "bg-blue-500/10"
        : "bg-zinc-500/10"

  const iconTextClass =
    state.status === "completed"
      ? "text-green-500"
      : state.status === "running"
        ? "text-blue-500"
        : "text-zinc-500"

  const badgeBorderClass =
    state.status === "completed"
      ? "border-green-500/30 text-green-500"
      : state.status === "running"
        ? "border-blue-500/30 text-blue-500"
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
          {state.iterations.map((iter, idx) => (
            <div
              key={idx}
              className="rounded-md border border-border/30 bg-muted/20 p-2.5 space-y-1.5"
            >
              <div className="flex items-center gap-2 flex-wrap">
                {iter.type === "tool_call" ? (
                  <>
                    <Wrench className="h-3 w-3 text-blue-500" />
                    <Badge
                      variant="outline"
                      className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
                    >
                      Tool
                    </Badge>
                    <span className="text-xs font-medium">
                      {iter.tool_name}
                    </span>
                  </>
                ) : (
                  <>
                    <Brain className="h-3 w-3 text-amber-500" />
                    <Badge
                      variant="outline"
                      className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
                    >
                      Thinking
                    </Badge>
                  </>
                )}
                <span className="text-[10px] text-muted-foreground">
                  Iteration {idx + 1}
                </span>
              </div>
              {iter.reasoning && (
                <p className="text-xs italic text-muted-foreground leading-relaxed">
                  {iter.reasoning}
                </p>
              )}
              {iter.tool_args &&
                Object.keys(iter.tool_args).length > 0 && (
                  <DagToolArgsBlock args={iter.tool_args} />
                )}
              {iter.loading && (
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span className="shiny-text">Executing...</span>
                </div>
              )}
              {iter.observation && (
                <div className="rounded bg-muted/30 border border-border/30 p-2">
                  <p className="text-[10px] font-medium text-muted-foreground mb-0.5 uppercase tracking-wider">
                    Observation
                  </p>
                  <pre className="whitespace-pre-wrap text-xs text-foreground/90 font-mono leading-relaxed max-h-[300px] overflow-y-auto">
                    {iter.observation}
                  </pre>
                </div>
              )}
              {iter.error && (
                <div className="rounded border border-destructive/30 bg-destructive/5 p-2">
                  <div className="flex items-center gap-1 mb-0.5">
                    <AlertCircle className="h-2.5 w-2.5 text-destructive" />
                    <p className="text-[10px] font-medium text-destructive uppercase tracking-wider">
                      Error
                    </p>
                  </div>
                  <pre className="whitespace-pre-wrap text-xs text-destructive/90 font-mono">
                    {iter.error}
                  </pre>
                </div>
              )}
            </div>
          ))}

          {/* Completed result */}
          {state.result && (
            <div className="rounded-md bg-muted/30 border border-border/30 p-3">
              <p className="text-[10px] font-medium text-muted-foreground mb-1 uppercase tracking-wider">
                Result
              </p>
              <MarkdownContent
                content={state.result}
                className="prose-sm text-sm text-foreground/90"
              />
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function DagToolArgsBlock({ args }: { args: Record<string, unknown> }) {
  if (typeof args.code === "string") {
    const rest = { ...args }
    delete rest.code
    const hasRest = Object.keys(rest).length > 0
    return (
      <div>
        <MarkdownContent
          content={`\`\`\`python\n${args.code}\n\`\`\``}
          className="text-[11px] [&_pre]:my-0 [&_pre]:p-2 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto"
        />
        {hasRest && (
          <MarkdownContent
            content={`\`\`\`json\n${JSON.stringify(rest, null, 2)}\n\`\`\``}
            className="text-[11px] [&_pre]:my-0 [&_pre]:p-2 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto mt-1"
          />
        )}
      </div>
    )
  }
  return (
    <MarkdownContent
      content={`\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``}
      className="text-[11px] [&_pre]:my-0 [&_pre]:p-2 [&_pre]:max-h-[300px] [&_pre]:overflow-y-auto"
    />
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
          <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
            <Clock className="h-2.5 w-2.5" />
            {fmtDuration(done.elapsed)}
          </span>
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
