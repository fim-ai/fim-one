"use client"

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { MarkdownContent } from "@/lib/markdown"
import { useState, useRef, useEffect, useLayoutEffect } from "react"
import { Loader2, Wrench, Brain, CheckCircle2, AlertCircle, Clock, RefreshCw, BarChart3, ChevronDown, ChevronUp, Sparkles } from "lucide-react"
import { fmtDuration } from "@/lib/utils"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"
import type { StepItem } from "@/hooks/use-react-steps"

interface ReactOutputProps {
  items: StepItem[]
}

export function ReactOutput({ items }: ReactOutputProps) {
  const [stepsExpanded, setStepsExpanded] = useState(false)

  const hasDone = items.some((i) => i.event === "done")
  const stepItems = items.filter((i) => i.event === "step")
  const doneItem = items.find((i) => i.event === "done")

  const toolCallCount = stepItems.filter((i) => {
    const step = i.data as ReactStepEvent
    return step.type === "tool_call"
  }).length

  const maxIteration = stepItems.reduce((max, i) => {
    const iter = i.displayIteration ?? 0
    return iter > max ? iter : max
  }, 0)

  const elapsed = doneItem ? (doneItem.data as ReactDoneEvent).elapsed : 0

  // After completion with tool calls: show collapsible summary bar + done card
  if (hasDone && toolCallCount > 0) {
    return (
      <div className="space-y-3 min-w-0 w-full">
        {/* Collapsible summary bar */}
        <button
          type="button"
          onClick={() => setStepsExpanded((v) => !v)}
          className="flex w-full items-center gap-2 px-4 py-2.5 rounded-lg border border-border/40 bg-muted/20 cursor-pointer hover:bg-muted/40 transition-colors text-xs text-muted-foreground"
        >
          <Wrench className="h-3.5 w-3.5 shrink-0" />
          <span>
            {toolCallCount} tool call{toolCallCount !== 1 ? "s" : ""}
            {" \u00b7 "}
            {maxIteration} iteration{maxIteration !== 1 ? "s" : ""}
            {" \u00b7 "}
            {fmtDuration(elapsed)}
          </span>
          {stepsExpanded ? (
            <ChevronUp className="h-3.5 w-3.5 ml-auto shrink-0" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5 ml-auto shrink-0" />
          )}
        </button>

        {/* Expanded step cards */}
        {stepsExpanded && (
          <div className="space-y-3 animate-in fade-in-0 slide-in-from-top-1 duration-200">
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

        {/* Done card */}
        {doneItem && (
          <div data-react-idx={items.indexOf(doneItem)}>
            <DoneCard done={doneItem.data as ReactDoneEvent} />
          </div>
        )}
      </div>
    )
  }

  // Streaming (no done yet) or direct answer (no steps): render as before
  return (
    <div className="space-y-3 min-w-0 w-full">
      {items.map((item, idx) => {
        if (item.event === "step") {
          const step = item.data as ReactStepEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <StepCard step={step} duration={item.duration} displayIteration={item.displayIteration} />
            </div>
          )
        }
        if (item.event === "done") {
          const done = item.data as ReactDoneEvent
          return (
            <div key={idx} data-react-idx={idx}>
              <DoneCard done={done} />
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

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
        className="flex items-center gap-1 mt-1 text-[10px] text-blue-400 hover:text-blue-300 transition-colors"
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

const THINKING_HINTS = [
  "Understanding your question...",
  "Analyzing the task...",
  "Selecting the best approach...",
  "Preparing tool calls...",
]

function ThinkingCard({ iterLabel, duration, reasoning }: { iterLabel: number; duration?: number; reasoning?: string }) {
  const [hintIdx, setHintIdx] = useState(0)
  const isWaiting = !reasoning && duration == null

  useEffect(() => {
    if (!isWaiting) return
    const timer = setInterval(() => {
      setHintIdx((i) => (i + 1) % THINKING_HINTS.length)
    }, 2000)
    return () => clearInterval(timer)
  }, [isWaiting])

  return (
    <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
      <CardContent className="space-y-2">
        <div className="flex items-center gap-3">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10">
            {isWaiting ? (
              <Sparkles className="h-3.5 w-3.5 text-amber-500 animate-pulse" />
            ) : (
              <Brain className="h-3.5 w-3.5 text-amber-500" />
            )}
          </div>
          <Badge
            variant="outline"
            className="border-amber-500/30 text-amber-500 text-[10px] uppercase tracking-wider"
          >
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
          <p className="text-xs text-muted-foreground leading-relaxed pl-9 transition-opacity duration-500">
            <Loader2 className="inline h-3 w-3 animate-spin mr-1.5 align-text-bottom" />
            {THINKING_HINTS[hintIdx]}
          </p>
        )}
        {reasoning && (
          <p className="text-xs italic text-muted-foreground leading-relaxed pl-9">
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

  if (step.type === "tool_start") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-blue-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-blue-500" />
            </div>
            <Badge
              variant="outline"
              className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
            >
              Tool
            </Badge>
            <span className="text-xs font-medium text-foreground">
              {step.tool_name}
            </span>
            <span className="text-xs text-muted-foreground">
              Iteration {iterLabel}
            </span>
          </div>
          {step.reasoning && (
            <p className="text-xs italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
          {step.tool_args && Object.keys(step.tool_args).length > 0 && (
            <ToolArgsBlock args={step.tool_args} className="ml-9" />
          )}
          <div className="flex items-center gap-2 ml-9 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="shiny-text">Executing...</span>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (step.type === "tool_call") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-blue-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-blue-500/10">
              <Wrench className="h-3.5 w-3.5 text-blue-500" />
            </div>
            <Badge
              variant="outline"
              className="border-blue-500/30 text-blue-500 text-[10px] uppercase tracking-wider"
            >
              Tool
            </Badge>
            <span className="text-xs font-medium text-foreground">
              {step.tool_name}
            </span>
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
          {step.reasoning && (
            <p className="text-xs italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
          {step.tool_args && Object.keys(step.tool_args).length > 0 && (
            <ToolArgsBlock args={step.tool_args} className="ml-9" />
          )}
          {step.observation && (
            <div className="rounded-md border border-border/50 bg-muted/30 p-3 ml-9">
              <p className="text-xs font-medium text-muted-foreground mb-1 uppercase tracking-wider">
                Observation
              </p>
              <CollapsibleBlock>
                <pre className="whitespace-pre-wrap text-xs text-foreground/90 font-mono leading-relaxed">
                  {step.observation}
                </pre>
              </CollapsibleBlock>
            </div>
          )}
          {step.error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 ml-9">
              <div className="flex items-center gap-1.5 mb-1">
                <AlertCircle className="h-3 w-3 text-destructive" />
                <p className="text-xs font-medium text-destructive uppercase tracking-wider">
                  Error
                </p>
              </div>
              <pre className="whitespace-pre-wrap text-xs text-destructive/90 font-mono">
                {step.error}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    )
  }

  return null
}

function ToolArgsBlock({
  args,
  className,
}: {
  args: Record<string, unknown>
  className?: string
}) {
  if (typeof args.code === "string") {
    const rest = { ...args }
    delete rest.code
    const hasRest = Object.keys(rest).length > 0
    return (
      <div className={className}>
        <CollapsibleBlock>
          <MarkdownContent
            content={`\`\`\`python\n${args.code}\n\`\`\``}
            className="text-xs [&_pre]:my-0 [&_pre]:p-3"
          />
        </CollapsibleBlock>
        {hasRest && (
          <CollapsibleBlock>
            <MarkdownContent
              content={`\`\`\`json\n${JSON.stringify(rest, null, 2)}\n\`\`\``}
              className="text-xs [&_pre]:my-0 [&_pre]:p-3 mt-2"
            />
          </CollapsibleBlock>
        )}
      </div>
    )
  }
  return (
    <CollapsibleBlock>
      <MarkdownContent
        content={`\`\`\`json\n${JSON.stringify(args, null, 2)}\n\`\`\``}
        className={`text-xs [&_pre]:my-0 [&_pre]:p-3 ${className ?? ""}`}
      />
    </CollapsibleBlock>
  )
}

function DoneCard({ done }: { done: ReactDoneEvent }) {
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
      </CardContent>
    </Card>
  )
}
