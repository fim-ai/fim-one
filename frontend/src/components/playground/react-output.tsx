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
import { Loader2, Wrench, Brain, CheckCircle2, AlertCircle, Clock, RefreshCw } from "lucide-react"
import type { SSEMessage } from "@/hooks/use-sse"
import type { ReactStepEvent, ReactDoneEvent } from "@/types/api"

interface ReactOutputProps {
  messages: SSEMessage[]
  isRunning: boolean
}

interface StepItem {
  event: string
  data: unknown
  duration?: number
  displayIteration?: number
}

export function ReactOutput({ messages, isRunning }: ReactOutputProps) {
  const items = useMemo(() => {
    const result: StepItem[] = []
    let iterCount = 0
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i]

      // Skip empty thinking steps (no reasoning = just a processing indicator)
      if (msg.event === "step") {
        const step = msg.data as ReactStepEvent
        if (step.type === "thinking" && !step.reasoning) {
          continue
        }
      }

      // Calculate duration: time between this event and the next event
      let duration: number | undefined
      if (i + 1 < messages.length) {
        duration = (messages[i + 1].timestamp - msg.timestamp) / 1000
      }

      // Sequential display iteration for step events
      let displayIteration: number | undefined
      if (msg.event === "step") {
        iterCount++
        displayIteration = iterCount
      }

      result.push({ event: msg.event, data: msg.data, duration, displayIteration })
    }
    return result
  }, [messages])

  if (items.length === 0 && !isRunning) {
    return null
  }

  return (
    <div className="space-y-3 min-w-0 w-full">
      {items.map((item, idx) => {
        if (item.event === "step") {
          const step = item.data as ReactStepEvent
          return <StepCard key={idx} step={step} duration={item.duration} displayIteration={item.displayIteration} />
        }
        if (item.event === "done") {
          const done = item.data as ReactDoneEvent
          return <DoneCard key={idx} done={done} />
        }
        return null
      })}
      {isRunning && (
        <div className="flex items-center gap-2 px-1 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span className="shiny-text">Processing...</span>
        </div>
      )}
    </div>
  )
}

function StepCard({ step, duration, displayIteration }: { step: ReactStepEvent; duration?: number; displayIteration?: number }) {
  const iterLabel = displayIteration ?? (step.iteration ?? 0) + 1

  if (step.type === "thinking") {
    return (
      <Card className="animate-in fade-in-0 slide-in-from-bottom-2 duration-300 border-amber-500/20 py-4">
        <CardContent className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-500/10">
              <Brain className="h-3.5 w-3.5 text-amber-500" />
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
                {duration.toFixed(1)}s
              </span>
            )}
          </div>
          {step.reasoning && (
            <p className="text-sm italic text-muted-foreground leading-relaxed pl-9">
              {step.reasoning}
            </p>
          )}
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
            <span className="text-sm font-medium text-foreground">
              {step.tool_name}
            </span>
            <span className="text-xs text-muted-foreground">
              Iteration {iterLabel}
            </span>
            {duration != null && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-muted-foreground">
                <Clock className="h-2.5 w-2.5" />
                {duration.toFixed(1)}s
              </span>
            )}
          </div>
          {step.reasoning && (
            <p className="text-sm italic text-muted-foreground leading-relaxed pl-9">
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
              <pre className="whitespace-pre-wrap text-sm text-foreground/90 font-mono leading-relaxed">
                {step.observation}
              </pre>
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
              <pre className="whitespace-pre-wrap text-sm text-destructive/90 font-mono">
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
        <MarkdownContent
          content={`\`\`\`python\n${args.code}\n\`\`\``}
          className="text-xs [&_pre]:my-0 [&_pre]:p-3"
        />
        {hasRest && (
          <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 text-xs font-mono leading-relaxed mt-2">
            {JSON.stringify(rest, null, 2)}
          </pre>
        )}
      </div>
    )
  }
  return (
    <pre
      className={`overflow-x-auto rounded-md bg-muted/50 p-3 text-xs font-mono leading-relaxed ${className ?? ""}`}
    >
      {JSON.stringify(args, null, 2)}
    </pre>
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
