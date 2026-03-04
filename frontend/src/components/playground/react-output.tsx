"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import { useState } from "react"
import { Loader2, Wrench, Brain, CheckCircle2, Clock, RefreshCw, BarChart3, ChevronDown, ChevronUp, User } from "lucide-react"
import { fmtDuration } from "@/lib/utils"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"
import type { StepItem } from "@/hooks/use-react-steps"
import { ReferencesSection } from "./references-section"
import { IterationCard } from "@/components/steps"
import type { IterationData } from "@/components/steps"
import { SuggestedFollowups } from "./suggested-followups"

interface ReactOutputProps {
  items: StepItem[]
  isStreaming?: boolean
  onSuggestionSelect?: (query: string) => void
}

export function ReactOutput({ items, isStreaming, onSuggestionSelect }: ReactOutputProps) {
  const [stepsExpanded, setStepsExpanded] = useState(false)

  const hasDone = items.some((i) => i.event === "done")
  const stepItems = items.filter((i) => i.event === "step")
  const doneItem = items.find((i) => i.event === "done")

  const toolCallCount = stepItems.filter((i) => {
    const step = i.data as ReactStepEvent
    return step.type === "tool_call"
  }).length

  const elapsed = doneItem ? (doneItem.data as ReactDoneEvent).elapsed : 0

  // After completion with tool calls: show collapsible summary bar + done card
  if (hasDone && toolCallCount > 0) {
    return (
      <div className="space-y-3 min-w-0 w-full">
        {/* Collapsible tool call group */}
        <div className="rounded-lg border border-border/40 bg-muted/20">
          <button
            type="button"
            onClick={() => setStepsExpanded((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-2.5 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground rounded-lg"
          >
            <Wrench className="h-3.5 w-3.5 shrink-0" />
            <span>
              {toolCallCount} tool call{toolCallCount !== 1 ? "s" : ""}
              {" \u00b7 "}
              {fmtDuration(elapsed)}
            </span>
            {stepsExpanded ? (
              <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
            )}
          </button>

          {/* Expanded step cards — nested inside the collapsible group */}
          {stepsExpanded && (
            <div className="space-y-3 px-4 pt-1 pb-3">
              {stepItems.map((item) => {
                const originalIdx = items.indexOf(item)
                const step = item.data as ReactStepEvent
                return (
                  <div key={originalIdx} data-react-idx={originalIdx}>
                    <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Inject events — always visible (they are user messages) */}
        {items.filter((i) => i.event === "inject").map((item) => {
          const originalIdx = items.indexOf(item)
          const injectData = item.data as { content: string }
          return (
            <div key={originalIdx} data-react-idx={originalIdx} className="flex gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <User className="h-3.5 w-3.5 text-primary" />
              </div>
              <div className="flex-1 pt-0.5">
                <p className="text-sm text-foreground">{injectData.content}</p>
              </div>
            </div>
          )
        })}

        {/* Done card */}
        {doneItem && (
          <div data-react-idx={items.indexOf(doneItem)}>
            <DoneCard done={doneItem.data as ReactDoneEvent} items={items} onSuggestionSelect={onSuggestionSelect} />
          </div>
        )}
      </div>
    )
  }

  // Streaming (no done yet) or direct answer (no steps): render as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {/* Initial loading indicator before any step events arrive */}
      {isStreaming && items.length === 0 && (
        <div className="flex items-center gap-3 px-1 py-2">
          <Loader2 className="h-4 w-4 animate-spin text-amber-500" />
          <span className="text-sm text-muted-foreground shiny-text">Processing...</span>
        </div>
      )}
      {items.map((item, idx) => {
        if (item.event === "step") {
          const step = item.data as ReactStepEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
            </div>
          )
        }
        if (item.event === "inject") {
          const injectData = item.data as { content: string }
          return (
            <div key={idx} data-react-idx={idx} className="flex gap-3">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
                <User className="h-3.5 w-3.5 text-primary" />
              </div>
              <div className="flex-1 pt-0.5">
                <p className="text-sm text-foreground">{injectData.content}</p>
              </div>
            </div>
          )
        }
        if (item.event === "done") {
          const done = item.data as ReactDoneEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <DoneCard done={done} items={items} onSuggestionSelect={onSuggestionSelect} />
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

function ThinkingCard({ iterLabel, duration, reasoning }: { iterLabel: number; duration?: number; reasoning?: string }) {
  const isWaiting = !reasoning && duration == null

  return (
    <Card className="border-amber-500/20 py-4">
      <CardContent className="space-y-2">
        <div className="flex items-center gap-3">
          <Badge
            variant="outline"
            className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider gap-1"
          >
            <Brain className="h-3 w-3" />
            Thinking
          </Badge>
          <span className="text-xs text-muted-foreground">
            Iteration {iterLabel}
          </span>
          {duration != null && (
            <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(duration)}
            </span>
          )}
        </div>
        {isWaiting && (
          <p className="text-xs text-muted-foreground leading-relaxed">
            <Loader2 className="inline h-3 w-3 animate-spin mr-1.5 align-text-bottom" />
            <span className="shiny-text">Processing...</span>
          </p>
        )}
        {reasoning && (
          <p className="text-xs italic text-muted-foreground leading-relaxed">
            {reasoning}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function StepCard({ step, duration, displayIteration }: { step: ReactStepEvent; duration?: number; displayIteration?: number }) {
  const iterLabel = displayIteration ?? (step.iteration ?? 0) + 1

  if (step.type === "thinking") {
    return <ThinkingCard iterLabel={iterLabel} duration={duration} reasoning={step.reasoning} />
  }

  // Map ReactStepEvent to IterationData for tool_start / tool_call
  const iterData: IterationData = {
    type: step.type,
    displayIteration: iterLabel,
    tool_name: step.tool_name,
    tool_args: step.tool_args,
    reasoning: step.reasoning,
    observation: step.observation,
    error: step.error,
    duration,
    loading: step.type === "tool_start",
  }

  return <IterationCard data={iterData} variant="card" defaultCollapsed={true} />
}

function DoneCard({ done, items, onSuggestionSelect }: { done: ReactDoneEvent; items?: StepItem[]; onSuggestionSelect?: (query: string) => void }) {
  return (
    <Card className="border-green-500/20 py-4">
      <CardHeader className="pb-0">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-green-500/10">
            <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          </div>
          <CardTitle className="text-sm">Result</CardTitle>
          <div className="ml-auto flex items-center gap-3 text-[10px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <RefreshCw className="h-2.5 w-2.5" />
              {done.iterations} iteration{done.iterations !== 1 ? "s" : ""}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="h-2.5 w-2.5" />
              {fmtDuration(done.elapsed)}
            </span>
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
        {items && <ReferencesSection items={items} />}
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
