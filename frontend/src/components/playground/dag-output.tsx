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
import { useState, useRef, useLayoutEffect } from "react"
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
            {stepStates.map((state) => (
              <div key={state.step_id} data-step-id={state.step_id}>
                <StepProgressCard state={state} />
              </div>
            ))}
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
          {state.iterations.map((iter, idx) => (
            <div
              key={idx}
              className="rounded-md border border-border/30 bg-muted/20 p-2.5 space-y-1.5"
            >
              <div className="flex items-center gap-2 flex-wrap">
                {iter.type === "tool_call" ? (
                  <>
                    <Wrench className="h-3 w-3 text-amber-500" />
                    <Badge
                      variant="outline"
                      className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
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
                  <CollapsibleBlock>
                    <pre className="whitespace-pre-wrap text-xs text-foreground/90 font-mono leading-relaxed">
                      {iter.observation}
                    </pre>
                  </CollapsibleBlock>
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
                className="text-xs text-foreground/80 [&_h1]:text-sm [&_h2]:text-xs [&_h3]:text-xs [&_h4]:text-xs [&_p]:text-xs [&_li]:text-xs [&_td]:text-xs [&_th]:text-xs [&_table]:text-xs"
              />
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function DagToolArgsBlock({ args }: { args: Record<string, unknown> }) {
  const mdCls = "text-[11px] [&_pre]:my-0 [&_pre]:p-2"
  if (typeof args.code === "string") {
    const rest = { ...args }
    delete rest.code
    const hasRest = Object.keys(rest).length > 0
    return (
      <div className="rounded bg-muted/30 border border-border/30 p-2">
        <p className="text-[10px] font-medium text-muted-foreground mb-0.5 uppercase tracking-wider">
          Arguments
        </p>
        <CollapsibleBlock>
          <MarkdownContent
            content={`\`\`\`python\n${args.code}\n\`\`\``}
            className={mdCls}
          />
        </CollapsibleBlock>
        {hasRest && (
          <div className="mt-1">
            <CollapsibleBlock>
              <MarkdownContent
                content={`\`\`\`json\n${JSON.stringify(rest, null, 2)}\n\`\`\``}
                className={mdCls}
              />
            </CollapsibleBlock>
          </div>
        )}
      </div>
    )
  }
  return (
    <div className="rounded bg-muted/30 border border-border/30 p-2">
      <p className="text-[10px] font-medium text-muted-foreground mb-0.5 uppercase tracking-wider">
        Arguments
      </p>
      <CollapsibleBlock>
        <MarkdownContent
          content={`\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``}
          className={mdCls}
        />
      </CollapsibleBlock>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Collapsible block (same as react-output)                           */
/* ------------------------------------------------------------------ */

const COLLAPSE_HEIGHT = 60

function CollapsibleBlock({
  children,
  maxHeight = COLLAPSE_HEIGHT,
}: {
  children: React.ReactNode
  maxHeight?: number
}) {
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const el = ref.current
    if (el) setOverflows(el.scrollHeight > maxHeight)
  }, [children, maxHeight])

  if (!overflows) {
    return <div ref={ref}>{children}</div>
  }

  return (
    <div>
      <div
        ref={ref}
        style={!expanded ? { maxHeight } : undefined}
        className={!expanded ? "overflow-hidden" : undefined}
      >
        {children}
      </div>
      {!expanded && (
        <div className="h-6 -mt-6 bg-gradient-to-t from-muted/80 to-transparent pointer-events-none relative z-[1]" />
      )}
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex items-center gap-1 mt-1 text-[10px] text-amber-400 hover:text-amber-300 transition-colors"
      >
        {expanded ? (
          <>
            <ChevronUp className="h-3 w-3" />
            Collapse
          </>
        ) : (
          <>
            <ChevronDown className="h-3 w-3" />
            Show more
          </>
        )}
      </button>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Shared components                                                  */
/* ------------------------------------------------------------------ */

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
