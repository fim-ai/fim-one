"use client"

import { useMemo } from "react"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import {
  Loader2,
  Wrench,
  Brain,
  CheckCircle2,
  AlertCircle,
  ListTree,
  Play,
  BarChart3,
  ArrowRight,
  Clock,
  Target,
  Gauge,
} from "lucide-react"
import type { SSEMessage } from "@/hooks/use-sse"
import type {
  DagPhaseEvent,
  DagStepProgressEvent,
  DagDoneEvent,
} from "@/types/api"

interface DagOutputProps {
  messages: SSEMessage[]
  isRunning: boolean
}

interface StepState {
  step_id: string
  task?: string
  status: "pending" | "running" | "completed"
  result?: string
  duration?: number
  iterations: Array<{
    type?: string
    iteration?: number
    tool_name?: string
    tool_args?: Record<string, unknown>
    reasoning?: string
    observation?: string
    error?: string
  }>
}

export function DagOutput({ messages, isRunning }: DagOutputProps) {
  const { planSteps, stepStates, analysisPhase, doneEvent, currentPhase } =
    useMemo(() => {
      let planSteps: DagPhaseEvent["steps"] = undefined
      const stepMap = new Map<string, StepState>()
      let analysisPhase: DagPhaseEvent | null = null
      let doneEvent: DagDoneEvent | null = null
      let currentPhase: string | null = null

      for (const msg of messages) {
        if (msg.event === "phase") {
          const phase = msg.data as DagPhaseEvent
          if (phase.name === "planning" && phase.status === "done" && phase.steps) {
            planSteps = phase.steps
            for (const s of phase.steps) {
              stepMap.set(s.id, {
                step_id: s.id,
                task: s.task,
                status: "pending",
                iterations: [],
              })
            }
          }
          if (phase.name === "executing") {
            currentPhase = "executing"
          }
          if (phase.name === "analyzing") {
            currentPhase = "analyzing"
            if (phase.status === "done") {
              analysisPhase = phase
            }
          }
          if (phase.name === "planning" && phase.status === "start") {
            currentPhase = "planning"
          }
        }

        if (msg.event === "step_progress") {
          const sp = msg.data as DagStepProgressEvent
          const existing = stepMap.get(sp.step_id)
          if (!existing) {
            stepMap.set(sp.step_id, {
              step_id: sp.step_id,
              task: sp.task,
              status: "pending",
              iterations: [],
            })
          }
          const state = stepMap.get(sp.step_id)!

          if (sp.task) state.task = sp.task

          if (sp.event === "started") {
            state.status = "running"
          } else if (sp.event === "completed") {
            state.status = "completed"
            if (sp.result) state.result = sp.result
            if (sp.duration) state.duration = sp.duration
          } else if (sp.event === "iteration") {
            state.iterations.push({
              type: sp.type,
              iteration: sp.iteration,
              tool_name: sp.tool_name,
              tool_args: sp.tool_args,
              reasoning: sp.reasoning,
              observation: sp.observation,
              error: sp.error,
            })
          }
        }

        if (msg.event === "done") {
          doneEvent = msg.data as DagDoneEvent
        }
      }

      return {
        planSteps,
        stepStates: Array.from(stepMap.values()),
        analysisPhase,
        doneEvent,
        currentPhase,
      }
    }, [messages])

  if (messages.length === 0 && !isRunning) {
    return null
  }

  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Planning spinner */}
      {currentPhase === "planning" && !planSteps && (
        <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
          <CardContent className="flex items-center gap-3">
            <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
            <span className="text-sm text-muted-foreground">
              Planning execution steps...
            </span>
          </CardContent>
        </Card>
      )}

      {/* Plan card */}
      {planSteps && planSteps.length > 0 && (
        <PlanCard steps={planSteps} />
      )}

      {/* Step progress cards */}
      {stepStates.length > 0 &&
        currentPhase !== "planning" &&
        stepStates.map((state) => (
          <StepProgressCard key={state.step_id} state={state} />
        ))}

      {/* Analysis phase */}
      {analysisPhase && <AnalysisCard phase={analysisPhase} />}

      {/* Done card */}
      {doneEvent && <DagDoneCard done={doneEvent} />}

      {/* Running indicator */}
      {isRunning && !doneEvent && (
        <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span className="shiny-text">
            {currentPhase === "executing"
              ? "Executing steps..."
              : currentPhase === "analyzing"
                ? "Analyzing results..."
                : "Processing..."}
          </span>
        </div>
      )}
    </div>
  )
}

function PlanCard({
  steps,
}: {
  steps: NonNullable<DagPhaseEvent["steps"]>
}) {
  return (
    <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <ListTree className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">
            Execution Plan
          </CardTitle>
          <Badge variant="secondary" className="text-[10px]">
            {steps.length} step{steps.length !== 1 ? "s" : ""}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {steps.map((step) => (
            <div
              key={step.id}
              className="rounded-md border border-border/50 bg-muted/30 p-3 space-y-1.5"
            >
              <div className="flex items-center gap-2">
                <Badge
                  variant="outline"
                  className="border-green-500/30 text-green-500 text-[10px] font-mono"
                >
                  {step.id}
                </Badge>
                {step.tool_hint && (
                  <Badge variant="secondary" className="text-[10px]">
                    {step.tool_hint}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-foreground/90">{step.task}</p>
              {step.deps.length > 0 && (
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  <ArrowRight className="h-3 w-3" />
                  <span>Depends on: {step.deps.join(", ")}</span>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function StepProgressCard({ state }: { state: StepState }) {
  const StatusIcon =
    state.status === "completed"
      ? CheckCircle2
      : state.status === "running"
        ? Play
        : Loader2

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
      className={`animate-in fade-in-0 slide-in-from-bottom-2 duration-300 py-4 ${cardBorderClass}`}
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
              {state.duration.toFixed(1)}s
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
              {iter.observation && (
                <div className="rounded bg-muted/30 border border-border/30 p-2">
                  <p className="text-[10px] font-medium text-muted-foreground mb-0.5 uppercase tracking-wider">
                    Observation
                  </p>
                  <pre className="whitespace-pre-wrap text-xs text-foreground/90 font-mono leading-relaxed">
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
          className="text-[11px] [&_pre]:my-0 [&_pre]:p-2"
        />
        {hasRest && (
          <pre className="overflow-x-auto rounded bg-muted/50 p-2 text-[11px] font-mono leading-relaxed mt-1">
            {JSON.stringify(rest, null, 2)}
          </pre>
        )}
      </div>
    )
  }
  return (
    <pre className="overflow-x-auto rounded bg-muted/50 p-2 text-[11px] font-mono leading-relaxed">
      {JSON.stringify(args, null, 2)}
    </pre>
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
            <span className={`flex items-center gap-1 ${done.achieved ? "text-green-500" : "text-destructive"}`}>
              <Target className="h-2.5 w-2.5" />
              {done.achieved ? "Achieved" : "Not Achieved"}
            </span>
            <span className="flex items-center gap-1">
              <Gauge className="h-2.5 w-2.5" />
              {(done.confidence * 100).toFixed(0)}%
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-2.5 w-2.5" />
              {done.elapsed.toFixed(1)}s
            </span>
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
